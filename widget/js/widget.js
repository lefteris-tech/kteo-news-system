/* =============================================================
 * Custom News RSS Widget — Yodeck
 * -------------------------------------------------------------
 * Vanilla JS, no dependencies. Uses native fetch() + DOMParser.
 * Implements the standard Yodeck HTML widget hooks:
 *   init_widget(config), show_widget(), start_widget(),
 *   hide_widget(),  stop_widget().
 * ============================================================= */

(function () {
    'use strict';

    // ---------- Defaults (overridden by Yodeck config) ----------
    const DEFAULTS = {
        rssURL:           '',
        defaultImage:     'assets/default-news.svg',
        layout:           'horizontal',     // horizontal | fullbleed | vertical
        theme:            'dark',           // dark | light
        accent_color:     '#ff5722',
        font_color:       '',               // optional override
        bg_color:         '',               // optional override
        font_size:        100,              // % scale of base size
        position:         null,             // 1-indexed; if set, single-item mode
        number_of_slides: 5,
        slide_duration:   10,               // seconds per slide
        refresh_rate:     30,               // minutes between feed refreshes
        date_locale:      'el-GR',          // 'el-GR' | 'en-US' | etc.
        show_date:        true,
        show_description: true,
        widget:           '0'
    };

    // ---------- Module state ----------
    const state = {
        config:        null,
        items:         [],
        currentIndex:  0,
        slideTimer:    null,
        refreshTimer:  null,
        isStarted:     false
    };

    // ---------- DOM refs ----------
    const dom = {
        body:    document.body,
        stage:   document.getElementById('stage'),
        overlay: document.getElementById('overlay'),
        errorEl: document.getElementById('error-screen')
    };

    /* =============================================================
     * YODECK HOOKS
     * =========================================================== */

    /**
     * Called by Yodeck right after the widget loads.
     * @param {Object} config — fields defined in schema.json
     */
    window.init_widget = function (config) {
        state.config = Object.assign({}, DEFAULTS, config || {});
        applyTheme();
        applyLayout();
        applyAccentAndFont();

        if (!state.config.rssURL) {
            showError('Δεν έχει οριστεί URL του RSS feed στις ρυθμίσεις του widget.');
            return;
        }

        fetchAndRender(/* isRefresh = */ false);
    };

    /** Called just before the widget appears on the screen. */
    window.show_widget = function () { /* nothing to do */ };

    /** Called just after the widget appears — start timers here. */
    window.start_widget = function () {
        if (state.isStarted) return;
        state.isStarted = true;
        startSlideRotation();
        startFeedRefresh();
    };

    /** Called just before the widget is removed from screen. */
    window.hide_widget = function () { /* nothing to do */ };

    /** Called just after the widget is removed — stop timers here. */
    window.stop_widget = function () {
        state.isStarted = false;
        stopSlideRotation();
        stopFeedRefresh();
    };

    /* =============================================================
     * THEME / LAYOUT / STYLE APPLICATION
     * =========================================================== */

    function applyTheme() {
        dom.body.classList.remove('theme-light', 'theme-dark');
        dom.body.classList.add('theme-' + (state.config.theme || 'dark'));
    }

    function applyLayout() {
        dom.body.classList.remove('layout-horizontal', 'layout-fullbleed', 'layout-vertical');
        dom.body.classList.add('layout-' + (state.config.layout || 'horizontal'));
    }

    function applyAccentAndFont() {
        const root = document.documentElement;
        if (state.config.accent_color) {
            root.style.setProperty('--accent', state.config.accent_color);
        }
        if (state.config.font_color) {
            root.style.setProperty('--text', state.config.font_color);
        }
        if (state.config.bg_color) {
            root.style.setProperty('--bg', state.config.bg_color);
            root.style.setProperty('--bg-elevated', state.config.bg_color);
        }
        // font_size as % scale → adjust root font-size of body
        const scale = parseInt(state.config.font_size, 10) || 100;
        dom.body.style.fontSize = scale + '%';
        // expose slide_duration to CSS for the progress-bar animation
        const dur = parseInt(state.config.slide_duration, 10) || 10;
        root.style.setProperty('--slide-duration', dur + 's');
    }

    /* =============================================================
     * FEED FETCH + PARSE + RENDER
     * =========================================================== */

    async function fetchAndRender(isRefresh) {
        try {
            const xmlText = await fetchFeed(state.config.rssURL);
            const items   = parseFeed(xmlText);

            // ---- Single-item (position) mode ----
            const pos = parseInt(state.config.position, 10);
            if (pos && pos >= 1) {
                if (items.length < pos) {
                    // Requested position doesn't exist in the feed
                    // → render nothing, let the Yodeck Layout background show through
                    state.items = [];
                    renderSlides();
                    hideOverlay();
                    return;
                }
                state.items        = items.slice(pos - 1, pos);
                state.currentIndex = 0;
                renderSlides();
                hideOverlay();
                return;
            }

            // ---- Multi-item rotation mode (existing) ----
            if (!items.length) {
                if (!isRefresh) {
                    showError('Το RSS feed δεν περιέχει άρθρα ή είναι σε μη υποστηριζόμενη μορφή.');
                }
                return;
            }

            // Limit to configured number_of_slides
            const max = parseInt(state.config.number_of_slides, 10) || 5;
            state.items        = items.slice(0, max);
            state.currentIndex = 0;

            renderSlides();
            hideOverlay();
        } catch (err) {
            console.error('[news-rss] fetch/parse failed:', err);
            if (!isRefresh) {
                showError(
                    'Δεν ήταν δυνατή η φόρτωση του RSS. ' +
                    'Ελέγξτε ότι το URL είναι σωστό και προσβάσιμο από τον player. ' +
                    '(' + (err.message || err) + ')'
                );
            }
        }
    }

    /** Fetch the RSS feed XML as text. */
    async function fetchFeed(url) {
        const response = await fetch(url, { cache: 'no-store' });
        if (!response.ok) {
            throw new Error('HTTP ' + response.status);
        }
        return await response.text();
    }

    /**
     * Parse RSS 2.0 or Atom feed text and return an array of normalized items.
     * Each item has: { title, description, pubDate (Date|null), image (url|null), link }
     */
    function parseFeed(xmlText) {
        const parser = new DOMParser();
        const doc    = parser.parseFromString(xmlText, 'application/xml');

        // detect parse errors
        if (doc.querySelector('parsererror')) {
            throw new Error('Το XML δεν είναι έγκυρο.');
        }

        // Channel-level fallback image (used if item has no image)
        const channelImage = getText(doc.querySelector('channel > image > url'))
            || getAttr(doc.querySelector('channel > itunes\\:image, channel > image'), 'href');

        // RSS 2.0 has <item>; Atom has <entry>
        const itemNodes = doc.querySelectorAll('item, entry');
        const out = [];

        itemNodes.forEach(function (node) {
            const title = stripHTML(
                getText(node.querySelector('title'))
            );

            // description / summary / content
            const rawDesc =
                getText(node.querySelector('description')) ||
                getText(node.querySelector('summary'))     ||
                getText(node.querySelector('content'))     ||
                getXMLText(node, 'content:encoded');

            const description = stripHTML(rawDesc);

            // pubDate / published / updated
            const dateStr =
                getText(node.querySelector('pubDate')) ||
                getText(node.querySelector('published')) ||
                getText(node.querySelector('updated')) ||
                getText(node.querySelector('date'));
            const pubDate = dateStr ? new Date(dateStr) : null;

            // link (RSS uses <link>text</link>; Atom uses <link href="..."/>)
            let link = getText(node.querySelector('link'));
            if (!link) {
                const linkEl = node.querySelector('link[href]');
                if (linkEl) link = linkEl.getAttribute('href');
            }

            const image = findImage(node, rawDesc) || channelImage || state.config.defaultImage;

            out.push({
                title:       title || '(Χωρίς τίτλο)',
                description: description,
                pubDate:     (pubDate && !isNaN(pubDate)) ? pubDate : null,
                image:       image,
                link:        link
            });
        });

        return out;
    }

    /**
     * Try multiple RSS image conventions. Returns URL string or null.
     * Priority:
     *   1. <enclosure type="image/*" url="...">
     *   2. <media:content url="..." medium="image"|type="image/*">
     *   3. <media:thumbnail url="...">
     *   4. <itunes:image href="...">
     *   5. <image><url>...</url></image>  (item-level, rare)
     *   6. First <img src="..."> inside description / content:encoded
     */
    function findImage(itemNode, rawDescription) {
        // 1. enclosure
        const encs = itemNode.querySelectorAll('enclosure');
        for (let i = 0; i < encs.length; i++) {
            const t = encs[i].getAttribute('type') || '';
            if (t.indexOf('image') === 0 || !t) {
                const u = encs[i].getAttribute('url');
                if (u) return u;
            }
        }

        // 2. media:content (any namespace)
        const mediaContents = getNSElements(itemNode, 'content');  // matches media:content
        for (let i = 0; i < mediaContents.length; i++) {
            const el = mediaContents[i];
            // only consider those that came from the media namespace
            if (el.tagName.indexOf('media:') !== 0 && el.localName !== 'content') continue;
            const t      = el.getAttribute('type')   || '';
            const medium = el.getAttribute('medium') || '';
            if (medium === 'image' || t.indexOf('image') === 0 || (!t && !medium)) {
                const u = el.getAttribute('url');
                if (u) return u;
            }
        }

        // 3. media:thumbnail
        const thumbs = getNSElements(itemNode, 'thumbnail');
        for (let i = 0; i < thumbs.length; i++) {
            const u = thumbs[i].getAttribute('url') || thumbs[i].getAttribute('href');
            if (u) return u;
        }

        // 4. itunes:image  (href attribute)
        const itunesImg = getNSElements(itemNode, 'image');
        for (let i = 0; i < itunesImg.length; i++) {
            const u = itunesImg[i].getAttribute('href');
            if (u) return u;
        }

        // 5. <image><url>...</url></image>
        const imageUrl = getText(itemNode.querySelector('image > url'));
        if (imageUrl) return imageUrl;

        // 6. parse first <img src="..."> from description HTML
        if (rawDescription) {
            const m = rawDescription.match(/<img[^>]+src=["']([^"']+)["']/i);
            if (m && m[1]) return m[1];
        }

        return null;
    }

    /* =============================================================
     * SLIDES
     * =========================================================== */

    function renderSlides() {
        dom.stage.innerHTML = '';

        state.items.forEach(function (item, idx) {
            const slide = document.createElement('article');
            slide.className = 'slide';
            slide.id        = 'slide-' + idx;

            // image
            const img = document.createElement('div');
            img.className = 'slide-image';
            img.style.backgroundImage = 'url("' + escapeUrl(item.image) + '")';
            // pre-load and fall back if image fails
            preloadImage(item.image, function (ok) {
                if (!ok) {
                    img.style.backgroundImage = 'url("' + escapeUrl(state.config.defaultImage) + '")';
                }
            });

            // content area
            const content = document.createElement('div');
            content.className = 'slide-content';

            // meta line (date)
            if (state.config.show_date && item.pubDate) {
                const meta = document.createElement('div');
                meta.className = 'slide-meta';
                meta.innerHTML =
                    '<span class="dot"></span>' +
                    '<span>' + escapeHTML(formatDate(item.pubDate)) + '</span>';
                content.appendChild(meta);
            }

            // title
            const title = document.createElement('h2');
            title.className = 'slide-title';
            title.textContent = item.title;
            content.appendChild(title);

            // description
            if (state.config.show_description && item.description) {
                const desc = document.createElement('p');
                desc.className = 'slide-description';
                desc.textContent = item.description;
                content.appendChild(desc);
            }

            slide.appendChild(img);
            slide.appendChild(content);

            // progress bar
            const progress = document.createElement('div');
            progress.className = 'slide-progress';
            slide.appendChild(progress);

            dom.stage.appendChild(slide);
        });

        // activate first slide
        if (state.items.length) {
            const first = document.getElementById('slide-0');
            if (first) first.classList.add('active');
            state.currentIndex = 0;
        }

        // After layout has settled, fit each slide's text to its container.
        // Removes mid-text "…" by shrinking font-size when content is too long.
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                dom.stage.querySelectorAll('.slide').forEach(fitTextToSlide);
            });
        });
    }

    /**
     * Auto-shrink the slide's font-size until the text content fits inside
     * its container. Prevents the "…" truncation when titles or summaries
     * are unusually long. Stops at MIN_FONT_SIZE_PX as readability floor.
     */
    function fitTextToSlide(slide) {
        const content = slide.querySelector('.slide-content');
        if (!content) return;

        const MIN_FONT_SIZE_PX = 16;
        const STEP_PX          = 1;
        const MAX_ITERATIONS   = 60;

        let iterations = 0;
        while (content.scrollHeight > content.clientHeight + 1 && iterations < MAX_ITERATIONS) {
            const current = parseFloat(getComputedStyle(slide).fontSize);
            if (current <= MIN_FONT_SIZE_PX) break;
            slide.style.fontSize = (current - STEP_PX) + 'px';
            iterations++;
        }
    }

    function showSlide(index) {
        const all = dom.stage.querySelectorAll('.slide');
        all.forEach(function (s) { s.classList.remove('active'); });

        const target = document.getElementById('slide-' + index);
        if (target) {
            // force reflow so the progress-bar animation restarts
            void target.offsetWidth;
            target.classList.add('active');
        }
    }

    function nextSlide() {
        if (!state.items.length) return;
        state.currentIndex = (state.currentIndex + 1) % state.items.length;
        showSlide(state.currentIndex);
    }

    /* =============================================================
     * TIMERS
     * =========================================================== */

    function startSlideRotation() {
        stopSlideRotation();
        if (state.items.length <= 1) return;
        const dur = (parseInt(state.config.slide_duration, 10) || 10) * 1000;
        state.slideTimer = setInterval(nextSlide, dur);
    }

    function stopSlideRotation() {
        if (state.slideTimer) {
            clearInterval(state.slideTimer);
            state.slideTimer = null;
        }
    }

    function startFeedRefresh() {
        stopFeedRefresh();
        const minutes = parseInt(state.config.refresh_rate, 10) || 30;
        const ms = Math.max(1, minutes) * 60 * 1000;
        state.refreshTimer = setInterval(function () {
            fetchAndRender(/* isRefresh = */ true);
        }, ms);
    }

    function stopFeedRefresh() {
        if (state.refreshTimer) {
            clearInterval(state.refreshTimer);
            state.refreshTimer = null;
        }
    }

    /* =============================================================
     * UI HELPERS
     * =========================================================== */

    function hideOverlay() {
        dom.overlay.classList.add('hidden');
    }

    function showError(message) {
        if (!dom.errorEl) return;
        dom.errorEl.querySelector('.error-message').textContent = message || '';
        dom.errorEl.hidden = false;
        hideOverlay();
    }

    /* =============================================================
     * UTILITIES
     * =========================================================== */

    function getText(node) {
        return (node && node.textContent) ? node.textContent.trim() : '';
    }

    function getAttr(node, name) {
        return (node && node.getAttribute) ? (node.getAttribute(name) || '') : '';
    }

    /** Get raw text including CDATA from any direct/indirect tag (e.g. content:encoded). */
    function getXMLText(parent, tagName) {
        // Try by tagName with namespace prefix as plain string
        const els = parent.getElementsByTagName(tagName);
        if (els && els.length) return els[0].textContent.trim();
        return '';
    }

    /** Find elements by local name regardless of namespace prefix (media:, itunes:, etc.) */
    function getNSElements(parent, localName) {
        // getElementsByTagNameNS('*', localName) returns all NS variants
        try {
            return parent.getElementsByTagNameNS('*', localName);
        } catch (_) {
            return [];
        }
    }

    /** Strip HTML tags and decode common entities for clean text display. */
    function stripHTML(html) {
        if (!html) return '';
        // remove tags
        let text = String(html).replace(/<[^>]*>/g, ' ');
        // decode common named entities + numeric ones
        const entities = {
            '&amp;':  '&',  '&lt;':   '<',  '&gt;':  '>',
            '&quot;': '"',  '&#39;':  "'",  '&apos;': "'",
            '&nbsp;': ' ',  '&laquo;':'«',  '&raquo;':'»',
            '&hellip;': '…','&mdash;': '—', '&ndash;': '–'
        };
        text = text.replace(/&[a-zA-Z]+;|&#\d+;/g, function (match) {
            if (entities[match]) return entities[match];
            const n = match.match(/&#(\d+);/);
            if (n) return String.fromCharCode(parseInt(n[1], 10));
            return match;
        });
        // collapse whitespace
        return text.replace(/\s+/g, ' ').trim();
    }

    function escapeHTML(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function escapeUrl(s) {
        return String(s).replace(/"/g, '%22');
    }

    function preloadImage(src, callback) {
        if (!src) { callback(false); return; }
        const img = new Image();
        img.onload  = function () { callback(true);  };
        img.onerror = function () { callback(false); };
        img.src = src;
    }

    /**
     * Format a Date as a relative-time string (e.g. "πριν από 2 ώρες"),
     * falling back to absolute date if the article is older than ~30 days.
     */
    function formatDate(date) {
        if (!date) return '';
        const locale = state.config.date_locale || 'el-GR';
        const diffMs = date - new Date();
        const diffSec = Math.round(diffMs / 1000);

        // Older than 30 days? show absolute date
        if (Math.abs(diffSec) > 60 * 60 * 24 * 30) {
            try {
                return new Intl.DateTimeFormat(locale, {
                    dateStyle: 'medium',
                    timeStyle: 'short'
                }).format(date);
            } catch (_) {
                return date.toLocaleString(locale);
            }
        }

        // Relative time
        try {
            const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });
            const abs = Math.abs(diffSec);
            if (abs < 60)             return rtf.format(Math.round(diffSec / 1),         'second');
            if (abs < 60 * 60)        return rtf.format(Math.round(diffSec / 60),        'minute');
            if (abs < 60 * 60 * 24)   return rtf.format(Math.round(diffSec / 3600),      'hour');
            return                            rtf.format(Math.round(diffSec / 86400),     'day');
        } catch (_) {
            return date.toLocaleString(locale);
        }
    }

    /* =============================================================
     * BROWSER TEST MODE
     * Lets you load index.html directly in a browser with ?rss=URL
     * for quick local testing outside the Yodeck player.
     * =========================================================== */
    document.addEventListener('DOMContentLoaded', function () {
        const params = new URLSearchParams(window.location.search);
        const testUrl = params.get('rss');
        if (testUrl) {
            init_widget({
                rssURL:           testUrl,
                layout:           params.get('layout') || 'horizontal',
                theme:            params.get('theme')  || 'dark',
                number_of_slides: parseInt(params.get('n'), 10) || 5,
                slide_duration:   parseInt(params.get('d'), 10) || 8,
                refresh_rate:     30
            });
            // mimic Yodeck's start hook so timers run
            setTimeout(start_widget, 1000);
        }
    });

})();
