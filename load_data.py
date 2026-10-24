import requests

def get_by_path(root, path):
    for k in path:
        if isinstance(root, dict):
            if k not in root:
                return None
            root = root[k]
        elif isinstance(root, list):
            if not isinstance(k, int) or k < 0 or k >= len(root):
                return None
            root = root[k]
        else:
            return None
    return root

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
