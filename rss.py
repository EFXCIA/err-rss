import os
import time

import arrow
import requests
import feedparser
from errbot import BotPlugin, botcmd, arg_botcmd


class Rss(BotPlugin):
    """RSS Feeder plugin for Errbot."""

    INTERVAL = 20
    FEEDS = {}

    def activate(self):
        super().activate()
        self.session = requests.Session()
        with open(os.path.expanduser('~/.err-rss.cfg')) as f:
            self.session.auth = tuple(f.read().splitlines())
        self.start_poller(self.INTERVAL, self.check_feeds)

    def read_feed(self, url):
        try:
            r = self.session.get(url)
            r.raise_for_status()
        except Exception:
            return None
        else:
            return feedparser.parse(r.text)

    def check_feeds(self):
        """Check for any new feed entries."""

        def since(t):
            t = arrow.get(t)
            return lambda e: arrow.get(e['published']) > t

        num_feeds = len(self.FEEDS)
        if num_feeds == 0:
            return

        if num_feeds == 1:
            msg = 'Checking {} feed...'
        else:
            msg = 'Checking {} feeds...'
        self.log.info(msg.format(num_feeds))

        responses = []
        for title, data in self.FEEDS.items():
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

            entry_msg = '[{title}]({link}) --- {when}'
            for entry in recent_entries:
                responses.append((entry, entry_msg.format(**entry)))

            newest, *__, oldest = feed['entries']

            if recent_entries:
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

        results = sorted(responses, key=lambda e: arrow.get(e[0]['published']))
        for entry, response in results:
            # Can't use yield here since there's no incoming message.
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
        feed = None
        for __ in range(3):
            while feed is None:
                feed = self.read_feed(url)
                time.sleep(1)
        if feed is None:
            return "/me couldn't find a feed at {}".format(url)

        name = feed['feed']['title']
        self.FEEDS[name] = {
            'url': url,
            'last_check': arrow.get(feed['entries'][0]['published']),
            'room': message.to
        }
        return '/me watching [{}]({})'.format(name, url)

    @botcmd
    @arg_botcmd('name')
    def rss_ignore(self, message, name):
        """Ignore a currently watched feed."""
        if name in self.FEEDS:
            data = self.FEEDS[name]
            del self.FEEDS[name]
            return '/me ignoring [{}]({})'.format(name, data['url'])
        else:
            return "/me whatchu talkin' bout?'"
