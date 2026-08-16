from http import cookiejar
from os import PathLike, fspath


def ck2dict(cookies_str: str) -> dict[str, str]:
    """将 cookies 字符串转换为字典

    :param cookies_str: cookies 字符串

    :return: 字典
    """
    res = {}
    if not cookies_str:
        return res
    for cookie in cookies_str.split(";"):
        name, value = cookie.strip().split("=", 1)
        res[name] = value
    return res


def save_cookies_with_netscape(
    cookies_str: str,
    file_path: str | PathLike[str],
    domain: str,
) -> None:
    """将 Cookie 请求头转换为 yt-dlp 可读取的 Netscape 文件。"""
    cookie_file = cookiejar.MozillaCookieJar(fspath(file_path))
    for cookie in cookies_str.split(";"):
        name, value = cookie.strip().split("=", 1)
        cookie_file.set_cookie(
            cookiejar.Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=f".{domain}",
                domain_specified=True,
                domain_initial_dot=False,
                path="/",
                path_specified=True,
                secure=True,
                expires=0,
                discard=True,
                comment=None,
                comment_url=None,
                rest={"HttpOnly": ""},
                rfc2109=False,
            )
        )
    cookie_file.save(ignore_discard=True, ignore_expires=True)
