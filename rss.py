import os
import time
import threading
import configparser
from urllib.parse import urlsplit

import arrow
import requests
import feedparser
from errbot import BotPlugin, botcmd, arg_botcmd


#: Path to ini file for containing username and password by wildcard domain.
CONFIG_FILE = '~/.err-rss.ini'


def since(target_time):
    target_time = arrow.get(target_time)
    return lambda entry: published_date(entry) > target_time


def published_date(entry):
    return entry.get('published')


class Rss(BotPlugin):
    """RSS Feeder plugin for Errbot."""

    INTERVAL = 20
    FEEDS = {}

    def activate(self):
        super().activate()
        self.session = requests.Session()
        self.read_ini(CONFIG_FILE)
        # Manually use a timer, since the poller implementation in errbot is
        # patently retarded (aka busted, broken, etc. etc.).
        self.checker = None
        self.check_feeds()

    def read_ini(self, filepath):
        self.ini = configparser.ConfigParser()
        self.ini.read(os.path.expanduser(filepath))

    def schedule_next_check(self):
        self.stop_checking_feeds()
        self.log.info('Scheduling next check in {}s'.format(self.interval))
        self.checker = threading.Timer(self.interval, self.check_feeds)
        self.checker.start()

    def stop_checking_feeds(self):
        if self.checker:
            self.log.info('Stopping any pending check')
            self.checker.cancel()

    def deactivate(self):
        super().deactivate()
        self.stop_checking_feeds()

    @property
    def interval(self):
        return self.INTERVAL

    @interval.setter
    def interval(self, value):
        if value > 0:
            self.log.info('New update interval: {}s'.format(value))
            self.INTERVAL = value
            self.schedule_next_check()
        else:
            self.INTERVAL = 0
            self.log.info('Halting the checking of feeds.')
            self.stop_checking_feeds()

    def read_feed(self, data, tries=3, patience=1):
        """Read the RSS/Atom feed at the given url.

        If no feed can be found at the given url, return None.

        :param str url: url at which to find the feed
        :return: parsed feed or None
        """
        if 'username' in data['config'] and 'password' in data['config']:
            username = data['config']['username']
            password = data['config']['password']
            self.session.auth = username, password

        tries_left = tries
        while tries_left:
            try:
                r = self.session.get(data['url'])
                r.raise_for_status()
                feed = feedparser.parse(r.text)
                assert 'title' in feed['feed']
                return feed
            except Exception as e:
                self.log.error(str(e))
                tries_left -= 1
                time.sleep(patience)
        return None

    def check_feeds(self, repeat=True):
        """Check for any new feed entries."""
        self.log.info('Starting feed checker...')
        # First, schedule the next check.
        if repeat:
            self.schedule_next_check()

        num_feeds = len(self.FEEDS)
        if num_feeds == 0:
            self.log.info('No feeds to check.')
            return

        if num_feeds == 1:
            feed_count_msg = 'Checking {} feed...'
        else:
            feed_count_msg = 'Checking {} feeds...'
        self.log.info(feed_count_msg.format(num_feeds))

        entries_to_report = []
        for title, data in self.FEEDS.items():  # TODO: make this thread safe
            feed = self.read_feed(data)
            if not feed:
                self.log.error('[{}] No feed found!'.format(title))
                continue

            # Touch up each entry.
            for entry in feed['entries']:
                entry['published'] = arrow.get(entry['published'])
                entry['when'] = entry['published'].humanize()
                entry['room'] = data['room']  # used to report in right room

            about_then = data['last_check'].humanize()
            recent_entries = tuple(filter(since(data['last_check']),
                                          feed['entries']))

            num_entries = len(feed['entries'])
            num_recent = len(recent_entries)
            newest, *__, oldest = feed['entries']

            if recent_entries:
                entries_to_report.extend(recent_entries)
                # Only update the last check time for this feed when there are
                # recent entries.
                data['last_check'] = newest['published']
                about_now = newest['when']

                if len(recent_entries) == 1:
                    found_msg = '[{}] Found {} entry since {}'
                else:
                    found_msg = '[{}] Found {} entries since {}'
                self.log.info(found_msg.format(title, num_recent, about_then))

                self.log.info('[{}] Updating last check time to {}'
                              .format(title, about_now))
            else:
                self.log.info('[{}] Found {} entries since {}, '
                              'but none since {}'.format(title, num_entries,
                                                         oldest['when'],
                                                         newest['when']))

        # Report results from all feeds in chronological order. Note we can't
        # use yield/return here since there's no incoming message.
        msg = '[{title}]({link}) --- {when}'
        for entry in sorted(entries_to_report, key=published_date):
            self.send(entry['room'], msg.format(**entry),
                      message_type='groupchat')

    @botcmd
    def rss_list(self, message, args):
        """List the currently watched feeds."""
        for title, data in self.FEEDS.items():
            last_check = arrow.get(data['last_check']).humanize()
            yield '/me [{}]({}) {}'.format(title, data['url'], last_check)
        else:
            yield ("/me You don't have any feeds :( "
                   "Add one by url (!rss watch <url>)")

    @botcmd
    @arg_botcmd('url', type=str)
    def rss_watch(self, message, url):
        """Watch a new feed."""
        # Find the first matching ini section using the domain of the url.s
        config = {}
        __, domain, *__ = urlsplit(url)
        self.log.debug('Finding ini section for domain "{}"...'.format(domain))
        for header, section in self.ini.items():
            tail = header.lstrip('*')
            if domain.endswith(tail):
                self.log.debug('Matched "{}" to "{}"'.format(domain, header))
                config = dict(section)
                break
            else:
                self.log.debug('"{}" is not a match'.format(header))

        data = {
            'url': url,
            'room': message.to,  # assumption: this is always a room
            'config': config
        }
        feed = self.read_feed(data)
        if feed is None:
            return "/me couldn't find a feed at {}".format(url)

        title = feed['feed']['title']
        if feed['entries']:
            data['last_check'] = arrow.get(feed['entries'][0]['published'])
        else:
            data['last_check'] = arrow.getnow()
        self.FEEDS[title] = data
        return '/me watching [{}]({})'.format(title, url)

    @botcmd
    @arg_botcmd('name', type=str)
    def rss_ignore(self, message, name):
        """Ignore a currently watched feed."""
        if name in self.FEEDS:
            data = self.FEEDS[name]
            del self.FEEDS[name]
            return '/me ignoring [{}]({})'.format(name, data['url'])
        else:
            return "/me whatchu talkin' bout?'"

    @botcmd
    def rss_interval(self, message, interval=None):
        """Get or set the polling interval."""
        if not interval:
            return '/me current interval is {}s'.format(self.interval)
        else:
            try:
                interval = int(interval)
            except ValueError:
                msg = ("/me That's not how this works. Give me a number of "
                        "seconds besides {} (that's what it is right now).")
                return msg.format(self.interval)
            if interval == self.interval:
                return '/me got it boss!'
            else:
                old = self.interval
                self.interval = interval
                return ('/me changed interval from '
                        '{}s to {}s'.format(old, self.interval))
