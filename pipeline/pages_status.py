"""Confirm the persisted commit was actually built by legacy GitHub Pages."""
import os
import time

import requests


def wait_for_build(repository, expected_sha, token, get=requests.get, sleep=time.sleep, attempts=30,
                   request_build=None, fallback_after=6):
    """Observe automatic builds first; request at most one fallback if absent.

    GITHUB_TOKEN pushes may not trigger Pages. An existing queued/building build
    for the exact SHA is sufficient to suppress the fallback, even if slow.
    """
    seen, requested = False, False
    for attempt in range(attempts):
        response = get(f'https://api.github.com/repos/{repository}/pages/builds/latest',
                       headers={'Authorization':f'Bearer {token}','Accept':'application/vnd.github+json'},timeout=20)
        response.raise_for_status()
        data = response.json()
        if data.get('commit') == expected_sha:
            seen = True
            if data.get('status') == 'built':
                print('Pages: commit persistido publicado e confirmado.')
                return
            if data.get('status') == 'errored':
                raise RuntimeError('Pages build failed for expected commit')
        if request_build is not None and not seen and not requested and attempt >= fallback_after:
            response = request_build(f'https://api.github.com/repos/{repository}/pages/builds',
                headers={'Authorization':f'Bearer {token}','Accept':'application/vnd.github+json'},timeout=20)
            response.raise_for_status()
            requested = True
        sleep(10)
    raise RuntimeError('Pages publication was not confirmed for persisted commit')


if __name__ == '__main__':
    wait_for_build(os.environ['GITHUB_REPOSITORY'],os.environ['EXPECTED_SHA'],os.environ['GH_TOKEN'],
                   request_build=requests.post)
