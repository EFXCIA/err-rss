Err RSS
===============================

An ErrBot plugin for RSS feeds.

Dependencies
------------

 * Requests
 * Feedparser
 * Arrow

Features
--------

 * Watch feeds by url
 * Ignore watched feeds by title
 * List watched feeds

Usage
-----

### Commands

    !rss watch <url>
    !rss list
    !rss ignore <title>
    !rss interval
    !rss interval 30

### Configuration

Configuration is accomplished via an INI file at `~/.err-rss.ini`. Err-RSS will
attempt to match the domain of each watched feed to a section of the INI file.
If a match is found, and the matched section contains values for username and
password, the values will be used for authentication when fetching updates from
the feed.

Note that the domain to section-header matching uses an "ends with" approach:

 * `http://www.google.com/rss` is matched by
	- `[www.google.com]`
	- `[*.google.com]`
	- `[.google.com]`
	- `[google.com]`
 * `http://www.google.com/rss` is *not* matched by
	- `[maps.google.com]`

Also note that wildcards are only supported if they are leading wildcards. For
example, `[www.*.com]` would *not* match anything *except* `http://www.*.com`
(literally).

Lastly, if more than one section matches, the first match is chosen.


License
-------

GNU General Public License v3 (GPLv3)

Authors
-------

 - Robert Grant <robert.grant@equifax.com>
