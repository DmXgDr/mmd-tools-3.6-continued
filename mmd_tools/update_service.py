"""GitHub release updates; no Blender API calls or credentials stored on disk."""
import ast
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

OWNER = 'DmXgDr'
REPOSITORY = 'mmd-tools-3.6-continued'
REPOSITORY_URL = 'https://github.com/%s/%s' % (OWNER, REPOSITORY)
API_ROOT = 'https://api.github.com/repos/%s/%s' % (OWNER, REPOSITORY)
MAX_DOWNLOAD = 128 * 1024 * 1024


class UpdateError(Exception):
    pass


def version_tuple(tag):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', tag)
    if not match:
        raise UpdateError('发布版本号必须为 v主版本.次版本.修订号')
    return tuple(map(int, match.groups()))


class _ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or not (host in ('github.com', 'api.github.com') or host.endswith('.githubusercontent.com')):
            raise UpdateError('下载跳转到了不受支持的地址')
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if host != urllib.parse.urlsplit(req.full_url).hostname:
            new.remove_header('Authorization')
        return new


def request_bytes(url, token='', limit=MAX_DOWNLOAD, binary=False):
    if not url.startswith(API_ROOT + '/'):
        raise UpdateError('更新地址不属于本插件仓库')
    headers = {'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json',
               'User-Agent': 'MMD-Tools-3.6-Continued', 'X-GitHub-Api-Version': '2022-11-28'}
    if token:
        headers['Authorization'] = 'Bearer ' + token.strip()
    try:
        opener = urllib.request.build_opener(_ReleaseRedirect())
        with opener.open(urllib.request.Request(url, headers=headers), timeout=25) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise UpdateError('更新文件超过大小限制')
        return data
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError('尚无可访问的正式发行版；私密仓库需要有读取权限的 GitHub 令牌') from None
        if exc.code in (401, 403):
            raise UpdateError('GitHub 身份验证失败、读取权限不足或请求次数受限') from None
        raise UpdateError('GitHub 请求失败：HTTP %d' % exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UpdateError('无法连接 GitHub，请检查网络后重试') from None


def check_release(token=''):
    release = json.loads(request_bytes(API_ROOT + '/releases/latest', token, limit=2*1024*1024))
    if release.get('draft') or release.get('prerelease'):
        raise UpdateError('只接收正式发行版')
    version = version_tuple(release.get('tag_name', ''))
    name = 'MMD-Tools-3.6-Continued-v%s.zip' % '.'.join(map(str, version))
    assets = {asset['name']: asset for asset in release.get('assets', [])}
    if name not in assets or 'SHA256SUMS.txt' not in assets:
        raise UpdateError('发行版缺少 Blender 3.6 安装包或 SHA256SUMS.txt')
    for asset in (assets[name], assets['SHA256SUMS.txt']):
        if not asset.get('url', '').startswith(API_ROOT + '/releases/assets/'):
            raise UpdateError('发行附件地址不正确')
    return {'tag': release['tag_name'], 'version': version, 'name': name,
            'asset_url': assets[name]['url'], 'checksum_url': assets['SHA256SUMS.txt']['url']}


def download_release(release, token=''):
    sums = request_bytes(release['checksum_url'], token, limit=65536, binary=True).decode('utf8')
    expected = None
    for line in sums.splitlines():
        match = re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?(.+)', line.strip())
        if match and match.group(2) == release['name']:
            expected = match.group(1).lower()
    if not expected:
        raise UpdateError('找不到安装包对应的校验值')
    data = request_bytes(release['asset_url'], token, binary=True)
    if hashlib.sha256(data).hexdigest() != expected:
        raise UpdateError('安装包校验失败，未修改当前插件')
    validate_archive(data, release['version']).close()
    return data


def validate_archive(data, expected_version):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
        if len(infos) > 5000 or sum(i.file_size for i in infos) > 256*1024*1024:
            raise UpdateError('安装包内容超过大小限制')
        names = set()
        for info in infos:
            path = PurePosixPath(info.filename)
            folded = info.filename.casefold().rstrip('/')
            if (not path.parts or path.parts[0] != 'mmd_tools' or path.is_absolute()
                    or '..' in path.parts or '\\' in info.orig_filename or ':' in info.filename or '\x00' in info.orig_filename
                    or any(p.endswith((' ', '.')) for p in path.parts)
                    or any(re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p, re.I) for p in path.parts)
                    or (info.external_attr >> 16) & 0o170000 == 0o120000 or folded in names):
                raise UpdateError('安装包包含不安全或重复的路径')
            names.add(folded)
        source = archive.read('mmd_tools/__init__.py').decode('utf-8-sig')
        info = None
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'bl_info' for t in node.targets):
                info = ast.literal_eval(node.value)
        if not info or tuple(info.get('version', ())) != tuple(expected_version):
            raise UpdateError('安装包版本与发行版本不一致')
        if tuple(info.get('blender', ()))[:2] != (3, 6):
            raise UpdateError('安装包不是 Blender 3.6 版本')
        bad = archive.testzip()
        if bad:
            raise UpdateError('安装包已损坏')
        return archive
    except (zipfile.BadZipFile, KeyError, SyntaxError, ValueError, UnicodeError) as exc:
        raise UpdateError('安装包结构或元数据无效') from exc


def install_archive(data, target, expected_version):
    target = Path(target).absolute()
    if target.name != 'mmd_tools' or target.is_symlink() or target.resolve() != target or not (target/'__init__.py').is_file():
        raise UpdateError('插件安装目录无效，未执行替换')
    archive = validate_archive(data, expected_version)
    work = Path(tempfile.mkdtemp(prefix='.mmd-update-', dir=str(target.parent)))
    backup = target.parent / ('.mmd-backup-' + time.strftime('%Y%m%d-%H%M%S') + '-' + work.name.rsplit('-', 1)[-1])
    try:
        archive.extractall(work)
        os.replace(target, backup)
        try:
            os.replace(work/'mmd_tools', target)
        except OSError:
            os.replace(backup, target)
            raise
        return str(backup)
    except OSError as exc:
        raise UpdateError('无法写入插件目录；已尽可能恢复原目录，请检查目录权限') from exc
    finally:
        archive.close()
        shutil.rmtree(work, ignore_errors=True)
