import os
import time
import threading
import configparser
from urllib.parse import urlsplit
from operator import itemgetter

import arrow
import requests
import feedparser
import dateutil.parser as dparser
from errbot import BotPlugin, botcmd, arg_botcmd


#: Path to ini file for containing username and password by wildcard domain.
CONFIG_FILE = '~/.err-rss.ini'


def since(target_time):
    target_time = arrow.get(target_time)
    return lambda entry: published_date(entry) > target_time


def published_date(entry):
    return entry.get('published')


def read_date(dt):
    """This reads a date in an unknown format."""
    return arrow.get(dparser.parse(dt))


def try_method(f):
    try:
        return f()
    except Exception as e:
        self.log.error('Thread failed with: {}'.format(str(e)))
        return None


class Rss(BotPlugin):
    """RSS Feeder plugin for Errbot."""

    INTERVAL = 20
    FEEDS = {}

    def activate(self):
        super().activate()
        self.session = requests.Session()
        self.read_ini(CONFIG_FILE)
        # Manually use a timer, since the poller implementation in errbot
        # breaks if you try to change the polling interval.
        self.checker = None
        then = arrow.get()
        self.delta = arrow.get() - then
        self.check_feeds()

    def deactivate(self):
        super().deactivate()
        self.stop_checking_feeds()

    def read_ini(self, path):
        """Read and store the configuration in the ini file at filepath.

        Note: this method silently fails if given a nonsensicle filepath, but
        it does log the number of sections it read.

        :param str path: path to the ini file to use for configuration
        """
        self.ini = configparser.ConfigParser()
        self.ini.read(os.path.expanduser(path))
        self.log.info('Read {} sections from {}'.format(len(self.ini), path))

    def schedule_next_check(self):
        """Schedule the next feed check.

        This method ensures any pending check for new feed entries is canceled
        before scheduling the next one.
        """
        self.stop_checking_feeds()
        if self.interval:
            job = lambda: try_method(self.check_feeds)
            self.checker = threading.Timer(self.interval, job)
            self.checker.start()
            self.log.info('Scheduled next check in {}s'.format(self.interval))
        else:
            self.log.info('Scheduling disabled since interval is 0s.')

    def stop_checking_feeds(self):
        """Stop any pending check for new feed entries."""
        if self.checker:
            self.checker.cancel()
            self.log.info('Pending check canceled.')
        else:
            self.log.info('No pending checks to cancel.')

    @property
    def interval(self):
        """Number of seconds between checks for new feed entries."""
        return self.INTERVAL

    @interval.setter
    def interval(self, value):
        if value > 0:
            self.log.info('New update interval: {}s'.format(value))
            self.INTERVAL = value
            self.schedule_next_check()
        else:
            self.INTERVAL = 0
            self.log.info('Scheduling disabled.')
            self.stop_checking_feeds()

    def read_feed(self, data, tries=3, patience=1):
        """Read the RSS/Atom feed at the given url.

        If no feed can be found at the given url, return None.

        :param str url: url at which to find the feed
        :param int tries: number of times to try fetching the feed
        :param int patience: number of seconds to wait in between tries
        :return: parsed feed or None
        """
        if 'username' in data['config'] and 'password' in data['config']:
            get_creds = itemgetter('username', 'password')
            self.session.auth = get_creds(data['config'])

        tries_left = tries
        while tries_left:
            try:
                response = self.session.get(data['url'])
                response.raise_for_status()
                feed = feedparser.parse(response.text)
                assert 'title' in feed['feed']
                return feed
            except Exception as e:
                self.log.error(str(e))
            tries_left -= 1
            time.sleep(patience)
        return None

    def check_feeds(self, repeat=True):
        """Check for any new feed entries.

        :param bool repeat: whether or not to schedule the next check
        """
        start_time = arrow.get()
        self.log.info('Starting feed checker...')

        # Make sure to extend the interval if the last feed check took longer
        # than the interval, then schedule the next check. Only problem with
        # this is that it requires two checks to overlap before any adjustment
        # is realized.
        if self.delta.seconds >= self.interval:
            self.log.info('Increasing the interval from {}s to {}s due to '
                          'longer processing times'.format(self.interval,
                                                           self.delta.seconds))
            self.interval = self.delta.seconds
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

            if not feed['entries']:
                self.log.info('[{}] No entries yet.'.format(title))
                continue

            # Touch up each entry.
            for entry in feed['entries']:
                entry['published'] = read_date(published_date(entry))
                entry['when'] = entry['published'].humanize()
                entry['rooms'] = data['rooms']  # used to report in right rooms

            # Find the oldest and newest entries for logging purposes.
            num_entries = len(feed['entries'])
            if num_entries == 1:
                newest = oldest = feed['entries'][0]
            elif num_entries == 2:
                newest, oldest = feed['entries']
            else:
                newest, *__, oldest = feed['entries']

            # Find recent entries
            is_recent = since(data['last_check'])
            recent_entries = tuple(e for e in feed['entries'] if is_recent(e))
            num_recent = len(recent_entries)

            if recent_entries:
                # Add recent entries to report
                entries_to_report.extend(recent_entries)
                if len(recent_entries) == 1:
                    found_msg = '[{}] Found {} entry since {}'
                else:
                    found_msg = '[{}] Found {} entries since {}'
                about_then = data['last_check'].humanize()
                self.log.info(found_msg.format(title, num_recent, about_then))

                # Only update the last check time for this feed when there are
                # recent entries.
                data['last_check'] = newest['published']
                self.log.info('[{}] Updated last check time to {}'
                              .format(title, newest['when']))
            else:
                found_msg = '[{}] Found {} entry since {}'
                self.log.info('[{}] Found {} entries since {}, '
                              'but none since {}'.format(title, num_entries,
                                                         oldest['when'],
                                                         newest['when']))

        # Report results from all feeds in chronological order. Note we can't
        # use yield/return here since there's no incoming message.
        msg = '[{title}]({link}) --- {when}'
        for entry in sorted(entries_to_report, key=published_date):
            for room in entry['rooms'].values():
                self.send(room.frm, msg.format(**entry), message_type=room.type)

        # Record the time needed for the current set of feeds.
        end_time = arrow.get()
        self.delta = end_time - start_time

    @botcmd
    def rss_list(self, message, args):
        """List the feeds being watched in this room."""

        def in_this_room(item):
            title, data = item
            return str(message.to) in data['rooms']

        for title, data in filter(in_this_room, self.FEEDS.items()):
            last_check = data['last_check'].humanize()
            yield '/me [{title}]({url}) {when}'.format(title=title,
                                                       url=data['url'],
                                                       when=last_check)
        else:
            yield '/me You have 0 feeds. Add one!'

    @botcmd
    @arg_botcmd('url', type=str)
    def rss_watch(self, message, url):
        """Watch a new feed by URL."""
        # Find the last matching ini section using the domain of the url.
        config = {}
        self.log.debug('Finding ini section for "{}"...'.format(url))
        for header, section in self.ini.items():
            if header_matches_url(header, url):
                config = dict(section)
                self.log.debug('Matched "{}" to "{}"'.format(url, header))
            else:
                self.log.debug('"{}" is not a match'.format(header))

        # Read in the feed.
        data = {'url': url, 'config': config, 'rooms': {}}
        feed = self.read_feed(data)
        if feed is None:
            return "/me couldn't find a feed at {}".format(url)

        # Establish feed metadata.
        title = feed['feed']['title']
        if title not in self.FEEDS:
            self.FEEDS[title] = data
            try:
                entry = feed['entries'][0]
            except IndexError:
                data['last_check'] = arrow.getnow()
            else:
                data['last_check'] = read_date(published_date(entry))
        self.FEEDS[title]['rooms'][message.frm.person] = message
        self.log.info('Watching {!r} for {!s}'.format(title, message.frm))

        # Report new feed watch
        return '/me watching [{}]({})'.format(title, url)

    @botcmd
    @arg_botcmd('title', type=str)
    def rss_ignore(self, message, title):
        """Ignore a currently watched feed by name."""
        feed = self.FEEDS.get(title)
        if feed and message.frm.person in feed['rooms']:
            del feed['rooms'][message.frm.person]
            if not feed['rooms']:
                del self.FEEDS[title]
            return '/me ignoring [{}]({})'.format(title, feed['url'])
        else:
            return "/me whatchu talkin' bout?"

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


def header_matches_url(header, url):
    # Here we compare the end of the domain and the start of the path (if
    # present) to the header.
    __, domain, path, *__ = urlsplit(url)
    parts = header.lstrip('*').split('/', 1)
    path = path.lstrip('/')
    if len(parts) == 2:
        # Domain and path in header. Match the path starts and domain ends.
        header_domain, header_path = parts
        return path.startswith(header_path) and domain.endswith(header_domain)
    else:
        # Domain without path. Match the domain ends.
        header_domain, = parts
        return domain.endswith(header_domain)
