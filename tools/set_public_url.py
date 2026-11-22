"""云端运行时，把站点公开地址写进 config.json（推送正文里的链接要用）。"""
import json
import os

repo = os.environ.get("GITHUB_REPOSITORY", "")
if "/" in repo:
    owner, name = repo.split("/", 1)
    url = f"https://{owner}.github.io/{name}/"
    with open("config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["public_url"] = url
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("public_url =", url)
else:
    print("未在 GitHub Actions 环境下运行，跳过")
