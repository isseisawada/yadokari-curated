"""HTTPアクセス層（robots.txt尊重・レート制限つき）。"""

from yadokari.net.client import HttpClient, RobotsDisallowed

__all__ = ["HttpClient", "RobotsDisallowed"]
