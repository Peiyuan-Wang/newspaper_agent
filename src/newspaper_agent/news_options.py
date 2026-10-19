"""Shared news output bounds; the number of candidates scored is independent."""
DEFAULT_NEWS_LIMIT = 10
MAX_NEWS_LIMIT = 100


def validate_news_limit(value):
    if type(value) is not int or not 1 <= value <= MAX_NEWS_LIMIT:
        raise ValueError(f'简报数量需为1到{MAX_NEWS_LIMIT}的整数')
    return value
