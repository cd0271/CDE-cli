import requests
import yaml
import os
from typing import Literal
from multi_download import multi_download
from db_load import *

# 获取系统架构
arch = os.environ.get("PROCESSOR_ARCHITECTURE")
arch = "x64" if arch == "AMD64" else "x86" if arch == "x86" else None
print("正在加载数据...")
cdes_tools = get_cdes_tools() # CDE Tools
tp_software = get_softw_link() # Third Party Software
source_list = get_source_list()
print("数据加载完成")

def get_latest_file(name, version=None, source="gitcode"):
    if name not in cdes_tools:
        print(f"工具 {name} 不存在")
        return None
    if source not in source_list:
        print(f"源 {source} 不存在")
        return None
    # Dict First Item
    dfi = lambda dct: next(iter(dct.items()))
    tp_var = cdes_tools[name][source_list[source][1]] # Temp Variable
    if version not in tp_var["tags"] and version is not None:
        print(f"版本 {version} 不存在")
        return None
    if version is None:
        tp_var2 = dfi(tp_var["tags"])[1]
    else:
        tp_var2 = tp_var["tags"][version]
    if "all" in tp_var2:
        tp_var3 = tp_var2["all"][0]
    elif arch == "x64":
        if "x64" in tp_var2:
            tp_var3 = tp_var2["x64"][0]
    elif arch == "x86":
        if "x86" in tp_var2:
            tp_var3 = tp_var2["x86"][0]
    if version is None:
        if source == "gitee": # Gitee没有latest的下载链接，直接返回指定版本的下载链接
            return f"{source_list[source][0]}/cd0271/{tp_var["name"]}/releases/download/{tp_var2}/{tp_var3}"
        return f"{source_list[source][0]}/cd0271/{tp_var["name"]}/releases/latest/download/{tp_var3}"
        # Github获取最新版本的下载链接
    else:
        return f"{source_list[source][0]}/cd0271/{tp_var["name"]}/releases/download/{version}/{tp_var3}"
        # Github获取指定版本的下载链接

def download_file(url, save_path, process=1024):
    """
    下载文件
    :param url: 下载链接
    :param save_path: 保存路径
    :param process: 并发进程数
    :return: 下载是否成功
    """
    try:
        multi_download(url, process, name=save_path)
        return "Success"
    except requests.Timeout:
        print(f"\n下载超时，源：{url}")
        return "Timeout"
    except requests.RequestException:
        print(f"\n下载失败，源：{url}")
        return "Failed"
    
def install_tool(name, version=None, source: Literal["gc", "ge", "gh"] ="gc") -> dict:
    """
    安装工具
    :param name: 工具名称
    :param version: 工具版本
    :return: 安装是否成功
    """
    # 遍历所有可用源，自动轮询重试
    if source == "gh":
        for k, v in source_list.items():
            if v[1] == "gh":
                # 获取当前源的下载链接
                dl_url = get_latest_file(name, version, k)
                if dl_url is None:
                    print("安装失败")
                    return {"Success": False, "Source": None}
                # 拼接保存路径
                save_path = f"/tools/{name}"
                # 开始下载
                result = download_file(dl_url, save_path, process=16)
                if result == "Success":
                    print(f"\n安装成功，源：{v[0]}")
                    return {"Success": True, "Source": v}
                elif result == "Timeout":
                    continue
                elif result == "Failed":
                    continue
    elif source == "gc":
        dl_url = get_latest_file(name, version, "gitcode")
    # 所有源全部尝试完毕，都失败
    print(f"\n{name} 所有源均下载失败，安装终止")
    return {"Success": False, "Source": None}
    