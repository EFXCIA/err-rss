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

        # Report number of feeds.
        num_feeds = len(self.FEEDS)
        msg = 'Checking {} feed'.format(num_feeds)
        if num_feeds != 1:
            msg += 's'
        self.log.debug(msg)

        for data in self.FEEDS.values():
            feed = self.read_feed(data['url'])
            if not feed:
                self.log.error('No feed found at {}'.format(data['url']))
                continue

            recent = since(data['last_check'])
            recent_entries = tuple(filter(recent, feed['entries']))
            
            num_entries = len(feed['entries'])
            num_recent = len(recent_entries)
            title = feed['feed']['title']
            self.log.debug('Found {}/{} entries in {}'.format(num_recent,
                                                              num_entries,
                                                              title))
            newest, *__, oldest = feed['entries']
            data['last_check'] = arrow.get(newest['published'])
            about_now = data['last_check'].humanize()
            self.log.debug('Updating last check time to {}'.format(about_now))
            
            responses = []
            for entry in recent_entries:
                responses.append('[{title}]({link})'.format(**entry))

            if responses:
                response = '/me {} --- {}'.format('\n'.join(responses),
                                                  about_now)
                self.send(data['room'], response, message_type='groupchat')
            else:
                newest = arrow.get(newest['published']).humanize()
                oldest = arrow.get(oldest['published']).humanize()
                response = 'Entries from {} to {}'.format(newest, oldest)
                self.log.debug(response)
                # self.send(data['room'], response, message_type='groupchat')

    @botcmd
    def rss_list(self, message, args):
        """List the currently watched feeds."""
        for url, name in self.FEEDS.items():
            yield '"{}" from <{}>'.format(url, name)

    @botcmd
    @arg_botcmd('url', type=str)
    def rss_watch(self, message, url):
        """Watch a new feed."""
        feed = None
        while feed is None:
            feed = self.read_feed(url)
            time.sleep(1)

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

    @botcmd
    def rss_test(self, message, args):
        import ipdb
        ipdb.set_trace()
