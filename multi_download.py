import os
import re
import shutil
import sys
import time
import requests
from multiprocessing import Process, Queue, current_process, freeze_support
from queue import Empty
from urllib.parse import unquote, urlsplit

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BLOCK = 256 * 1024
RETRIES = 5


def _human(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{int(n)}B" if u == "B" else f"{n:.1f}{u}"
        n /= 1024.0


def _fmt_time(sec):
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _draw(done, total, t0, width=28, tail="", base=0):
    el = time.time() - t0
    sp = (done - base) / el if el > 0 else 0.0
    p = min(max((done / total) if total else 0.0, 0.0), 1.0)
    fill = int(width * p)
    eta = ((total - done) / sp) if (sp > 0 and total) else -1
    sys.stdout.write(
        "\r  [{}{}] {:5.1f}%  {}/{}  {:>9}/s  ETA {:>7}  {}".format(
            "#" * fill, "." * (width - fill), p * 100,
            _human(done), _human(total), _human(sp),
            "-" if eta < 0 else _fmt_time(eta), tail))
    sys.stdout.flush()


def _guess_name(url, headers):
    cd = headers.get("Content-Disposition", "")
    m = re.search(r"filename\*\s*=\s*[^']*''([^;\"]+)", cd, re.I)
    if not m:
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
    if m:
        name = unquote(m.group(1).strip().strip('"'))
        if name:
            return name
    name = os.path.basename(urlsplit(url).path.rstrip("/"))
    return name or "download.bin"


def _probe(url):
    """返回 (size, support_range, headers)。只有真回 206 才算支持分段。"""
    hdr = {"User-Agent": UA, "Range": "bytes=0-0"}
    with requests.get(url, headers=hdr, stream=True, timeout=(10, 30)) as r:
        r.raise_for_status()
        h = dict(r.headers)
        code = r.status_code

    size = None
    cr = h.get("Content-Range", "")
    if "/" in cr:
        t = cr.rsplit("/", 1)[1].strip()
        if t.isdigit():
            size = int(t)
    if size is None and code == 200 and h.get("Content-Length", "").isdigit():
        size = int(h["Content-Length"])

    return size, (code == 206) and (size is not None), h


def _worker(idx, url, start, end, part, q, retries):
    expect = end - start + 1
    done = min(os.path.getsize(part), expect) if os.path.exists(part) else 0

    attempt = 0
    with open(part, "ab" if done else "wb") as f:
        while done < expect:
            attempt += 1
            if attempt > retries:
                q.put(("err", idx, f"分块{idx}重试{retries}次仍失败"))
                return
            try:
                h = {"User-Agent": UA, "Range": f"bytes={start + done}-{end}"}
                with requests.get(url, headers=h, stream=True,
                                  timeout=(10, 60)) as r:
                    if r.status_code == 200:
                        raise IOError("服务器忽略 Range，返回 200 全量")
                    if r.status_code != 206:
                        raise IOError(f"HTTP {r.status_code}")
                    n = 0
                    for data in r.iter_content(BLOCK):
                        if not data:
                            continue
                        f.write(data)
                        f.flush()
                        n += len(data)
                        done += len(data)
                        q.put(("p", idx, len(data)))
                        if done >= expect:
                            break
                    if n == 0 and done < expect:
                        raise IOError("连接返回 0 字节")
            except Exception:
                time.sleep(min(2 * attempt, 10))

    if done >= expect:
        q.put(("d", idx, None))
    else:
        q.put(("err", idx, f"分块{idx}数据不完整 {done}/{expect}"))


def _single(url, out, retries):
    part = out + ".part"
    done = os.path.getsize(part) if os.path.exists(part) else 0
    start_done = done
    attempt = 0
    t0 = time.time()
    while True:
        try:
            h = {"User-Agent": UA}
            if done:
                h["Range"] = f"bytes={done}-"
            with requests.get(url, headers=h, stream=True,
                              timeout=(10, 60)) as r:
                if done and r.status_code != 206:
                    done = 0
                    start_done = 0
                r.raise_for_status()
                cl = r.headers.get("Content-Length", "")
                total = done + int(cl) if cl.isdigit() else None
                with open(part, "ab" if done else "wb") as f:
                    for data in r.iter_content(BLOCK):
                        if not data:
                            continue
                        f.write(data)
                        f.flush()
                        done += len(data)
                        _draw(done, total, t0, base=start_done)
            break
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            print(f"[i] 已中断，{part} 可续传")
            raise
        except Exception as e:
            attempt += 1
            if attempt > retries:
                sys.stdout.write("\n")
                raise IOError(f"下载失败：{e}（已保留 {part} 可续传）")
            time.sleep(min(2 * attempt, 10))
    sys.stdout.write("\n")
    os.replace(part, out)


def _merge(parts, out):
    with open(out, "wb") as w:
        for p in parts:
            with open(p, "rb") as r:
                shutil.copyfileobj(r, w, 4 << 20)


def _multi(url, out, size, process, retries):
    n = max(1, min(int(process), os.cpu_count() or 1, 32))
    base, rest = divmod(size, n)
    segs, s = [], 0
    for i in range(n):
        ln = base + (1 if i < rest else 0)
        if ln <= 0:
            break
        segs.append((s, s + ln - 1))
        s += ln

    parts = [f"{out}.part{i}" for i in range(len(segs))]
    q = Queue()
    ps = [Process(target=_worker,
                  args=(i, url, segs[i][0], segs[i][1], parts[i], q, retries),
                  daemon=True)
          for i in range(len(segs))]
    for p in ps:
        p.start()

    done = sum(min(os.path.getsize(p), e - st + 1)
               for p, (st, e) in zip(parts, segs) if os.path.exists(p))
    start_done = done
    t0 = time.time()
    finished = 0
    errors = []

    try:
        while finished < len(ps):
            try:
                typ, i, val = q.get(timeout=0.1)
                if typ == "p":
                    done += val
                elif typ == "d":
                    finished += 1
                elif typ == "err":
                    errors.append(val)
                    finished += 1
            except Empty:
                pass
            _draw(done, size, t0, tail=f"{finished}/{len(ps)} 块",
                  base=start_done)
            if not any(p.is_alive() for p in ps) and finished < len(ps):
                codes = [p.exitcode for p in ps]
                errors.append(f"子进程异常退出 exitcode={codes}"
                              "（若从别的脚本调用，必须放在 "
                              "if __name__ == '__main__': 下）")
                break
    except KeyboardInterrupt:
        for p in ps:
            p.terminate()
        sys.stdout.write("\n")
        print("[i] 已中断，.partN 可续传")
        raise

    for p in ps:
        p.join(timeout=3)
    sys.stdout.write("\n")
    if errors:
        raise IOError("；".join(errors) + "（.part 已保留，可重跑续传）")

    _merge(parts, out)
    for p in parts:
        try:
            os.remove(p)
        except OSError:
            pass
    return out


def multi_download(url, process, name=None):
    """多进程分块下载 url；process 为进程数；name 为输出路径（None 自动取文件名）。

    返回最终文件绝对路径；失败抛 IOError（.part 保留可续传）。
    """
    if current_process().name != "MainProcess":
        raise RuntimeError(
            "multi_download 不能在子进程中重复调用；"
            "外部脚本请写在 if __name__ == '__main__': 之下")

    freeze_support()
    size, support, hdr = _probe(url)
    out = os.path.abspath(name or _guess_name(url, hdr))
    d = os.path.dirname(out)
    if d:
        os.makedirs(d, exist_ok=True)

    print(f"[*] 目标：{out}")
    print(f"[*] 大小：{_human(size) if size else '未知'}  "
          f"分段支持：{'是' if support else '否'}")

    if not support:
        print("[!] 不支持 Range 或大小未知，退回单进程整包下载")
        _single(url, out, RETRIES)
    else:
        _multi(url, out, size, process, RETRIES)

    got = os.path.getsize(out)
    if size and got != size:
        print(f"[!] 大小不一致：{got} != {size}")
    print(f"[+] 完成：{out}  {_human(got)}")
    return out
