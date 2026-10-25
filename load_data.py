import requests

def load_db(url):
    """
    加载json数据
    :param url: json数据的url
    :return: json数据
    """
    try:
        resp = requests.get(url)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return {}
