import os
import time
import threading

import arrow
import requests
import feedparser
from errbot import BotPlugin, botcmd, arg_botcmd


# TODO: Use an ini for user/pass by domain

#: Path to config file for containing username and password.
CONFIG_FILE = '~/.err-rss.cfg'


def since(t):
    t = arrow.get(t)
    return lambda e: arrow.get(e['published']) > t


class Rss(BotPlugin):
    """RSS Feeder plugin for Errbot."""

    INTERVAL = 20
    FEEDS = {}

    def activate(self):
        super().activate()
        self.session = requests.Session()
        with open(os.path.expanduser(CONFIG_FILE)) as f:
            self.session.auth = tuple(f.read().splitlines())
        # Manually use a timer, since the poller implementation in errbot is
        # patently retarded (aka busted, broken, etc. etc.).
        self.checker = None
        self.check_feeds()

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

    def read_feed(self, url, tries=3, patience=1):
        """Read the RSS/Atom feed at the given url.

        If no feed can be found at the given url, return None.

        :param str url: url at which to find the feed
        :return: parsed feed or None
        """
        tries_left = tries
        while tries_left:
            try:
                r = self.session.get(url)
                r.raise_for_status()
                feed = feedparser.parse(r.text)
                assert 'title' in feed['feed']
                return feed
            except Exception:
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

        responses = []
        for title, data in self.FEEDS.items():  # TODO: make this thread safe
            feed = self.read_feed(data['url'])
            if not feed:
                self.log.error('[{}] No feed found!'.format(title))
                continue

            for entry in feed['entries']:
                entry['when'] = arrow.get(entry['published']).humanize()

            about_then = data['last_check'].humanize()
            recent_entries = tuple(filter(since(data['last_check']),
                                          feed['entries']))

            num_entries = len(feed['entries'])
            num_recent = len(recent_entries)

            # Create the entry-response pair now. They'll be sorted and
            # reported along with those from other feeds.
            entry_msg = '[{title}]({link}) --- {when}'
            for entry in recent_entries:
                responses.append((entry, entry_msg.format(**entry)))

            newest, *__, oldest = feed['entries']

            if recent_entries:
                # Only update the last check time for this feed when there are
                # recent entries.
                data['last_check'] = arrow.get(newest['published'])
                about_now = data['last_check'].humanize()

                if len(recent_entries) == 1:
                    found_msg = '[{}] Found {} entry since {}'
                else:
                    found_msg = '[{}] Found {} entries since {}'
                self.log.info(found_msg.format(title, num_recent, about_then))

                last_check_update_msg = '[{}] Updating last check time to {}'
                self.log.info(last_check_update_msg.format(title, about_now))
            else:
                none_msg = '[{}] Found {} entries since {}, but none since {}'
                self.log.info(none_msg.format(title, num_entries,
                                              oldest['when'], newest['when']))

        # Report results from all feeds in chronological order.
        results = sorted(responses, key=lambda e: arrow.get(e[0]['published']))
        for entry, response in results:
            # Can't use yield/return here since there's no incoming message.
            self.send(data['room'], response, message_type='groupchat')

    @botcmd
    def rss_list(self, message, args):
        """List the currently watched feeds."""
        feeds = []
        for name, data in self.FEEDS.items():
            last_check = arrow.get(data['last_check']).humanize()
            yield '/me [{}]({}) {}'.format(name, data['url'], last_check)
        else:
            yield "/me You haven't added any feeds :("

    @botcmd
    @arg_botcmd('url', type=str)
    def rss_watch(self, message, url):
        """Watch a new feed."""
        feed = self.read_feed(url)
        if feed is None:
            return "/me couldn't find a feed at {}".format(url)

        title = feed['feed']['title']
        self.FEEDS[title] = {
            'url': url,
            'last_check': arrow.get(feed['entries'][0]['published']),
            'room': message.to  # assumption: this is always a room
        }
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
                return '/me changed interval from {}s to {}s'.format(old,
                                                                     interval)
