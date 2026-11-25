#!/usr/bin/env python3
# db_load.py = 从 TiDB 读取 CDE 数据，返回与原 YAML 完全相同的结构，替换原来的 load_data.py
# 连接信息：优先读同目录 tidb.yaml，其次读环境变量 TIDB_HOST / TIDB_USER / TIDB_PASSWORD

import sys

sys.dont_write_bytecode = True

import os
from pathlib import Path

import pymysql
import yaml

_conn = None


def conf():
    cfg = {}
    p = Path(__file__).resolve().with_name("tidb.yaml")
    if p.exists():
        with open(p, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    for key, env in (
        ("host", "TIDB_HOST"),
        ("port", "TIDB_PORT"),
        ("user", "TIDB_USER"),
        ("password", "TIDB_PASSWORD"),
        ("database", "TIDB_DB"),
        ("ssl_ca", "TIDB_SSL_CA"),
    ):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    if not (cfg.get("host") and cfg.get("user") and cfg.get("password")):
        raise RuntimeError("缺少连接信息：在同目录放 tidb.yaml，或设置 TIDB_HOST / TIDB_USER / TIDB_PASSWORD")
    return cfg


def conn():
    global _conn
    if _conn is None:
        cfg = conf()
        kw = dict(
            host=cfg["host"],
            port=int(cfg.get("port", 4000)),
            user=cfg["user"],
            password=cfg["password"],
            database=cfg.get("database", "test"),
            charset="utf8mb4",
            autocommit=True,
            ssl_verify_cert=True,
        )
        ca = cfg.get("ssl_ca")
        if ca:
            kw["ssl_ca"] = ca
        _conn = pymysql.connect(**kw)
    return _conn


def query(sql, args=None, dict_rows=True):
    global _conn
    for attempt in (0, 1):
        try:
            cur_class = pymysql.cursors.DictCursor if dict_rows else pymysql.cursors.Cursor
            with conn().cursor(cur_class) as cur:
                cur.execute(sql, args)
                return cur.fetchall()
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError):
            _conn = None
            if attempt:
                raise


def get_source_list():
    rows = query("SELECT source, url, code FROM source_list")
    return {r["source"]: [r["url"], r["code"]] for r in rows}


def get_cl5v():
    rows = query("SELECT version FROM cl5v ORDER BY version")
    return [r["version"] for r in rows]


def get_softw_link(package=None):
    if package:
        rows = query("SELECT package, arch, version, url FROM softw_link WHERE package=%s", (package,))
    else:
        rows = query("SELECT package, arch, version, url FROM softw_link")
    data = {}
    for r in rows:
        data.setdefault(r["package"], {}).setdefault(r["arch"], {})[r["version"]] = r["url"]
    return data


def get_cdes_tools(package=None):
    if package:
        rows = query(
            "SELECT package, platform, repo, tag, arch, file_name FROM cdes_tools WHERE package=%s", (package,)
        )
    else:
        rows = query("SELECT package, platform, repo, tag, arch, file_name FROM cdes_tools")
    data = {}
    for r in rows:
        plats = data.setdefault(r["package"], {})
        info = plats.setdefault(r["platform"], {"name": r["repo"], "tags": {}})
        info["tags"].setdefault(r["tag"], {}).setdefault(r["arch"], []).append(r["file_name"])
    return data
