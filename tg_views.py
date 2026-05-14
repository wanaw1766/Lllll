import requests
from re import search

class Api():
    real_views, proxy_errors, token_errors = 0, 0, 0

    def __init__(self, channel, post):
        self.url = 'https://t.me/'
        self.channel = channel
        self.post = post

    @classmethod
    def views(cls, self):
        """Fetch current view count – unchanged, but added error handling."""
        try:
            r = requests.get(
                f'{self.url}{self.channel}/{self.post}',
                params={'embed': '1', 'mode': 'tme'},
                headers={
                    'Referer': f'{self.url}{self.channel}/{self.post}',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                },
                timeout=10
            )
            match = search(r'<span class="tgme_widget_message_views">([^<]+)', r.text)
            if match:
                cls.real_views = match.group(1)
            else:
                # fallback
                match = search(r'data-view="([^"]+)"', r.text)
                if match:
                    cls.real_views = match.group(1)
        except Exception:
            pass

    def send_view(self, proxy, proxy_type):
        try:
            session = requests.Session()
            # 1. Get the page and extract the view token
            resp = session.get(
                f'{self.url}{self.channel}/{self.post}',
                params={'embed': '1', 'mode': 'tme'},
                headers={
                    'Referer': f'{self.url}{self.channel}/{self.post}',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                },
                proxies={
                    'http': f'{proxy_type}://{proxy}',
                    'https': f'{proxy_type}://{proxy}'
                },
                timeout=15
            )
            # Extract view token
            token_match = search(r'data-view="([^"]+)"', resp.text)
            if not token_match:
                Api.token_errors += 1
                # Debug: print a snippet of the response to logs
                print(f"Token extraction failed for {proxy}. Response snippet: {resp.text[:200]}")
                return
            view_token = token_match.group(1)

            # 2. Send the actual view request
            cookies_dict = session.cookies.get_dict()
            view_resp = session.get(
                'https://t.me/v/',
                params={'views': view_token},
                headers={
                    'Referer': f'https://t.me/{self.channel}/{self.post}?embed=1&mode=tme',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'X-Requested-With': 'XMLHttpRequest'
                },
                cookies={
                    'stel_dt': '-240',
                    'stel_web_auth': 'https%3A%2F%2Fweb.telegram.org%2Fz%2F',
                    'stel_ssid': cookies_dict.get('stel_ssid', ''),
                    'stel_on': cookies_dict.get('stel_on', '')
                },
                proxies={
                    'http': f'{proxy_type}://{proxy}',
                    'https': f'{proxy_type}://{proxy}'
                },
                timeout=15
            )
            # Optional: check if view was accepted
            if view_resp.status_code != 200:
                Api.proxy_errors += 1
        except Exception as e:
            Api.proxy_errors += 1
            # Uncomment for debugging:
            # print(f"Error with proxy {proxy}: {e}")
