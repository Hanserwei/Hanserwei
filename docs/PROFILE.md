# 主页维护说明

主页按 `profile-preview-v2.html` 的设计落地：纸色横幅、陶土色引号、个人介绍、最新文章和 RSS 订阅入口。正文使用 GitHub 原生主题；GitHub README 不能加载预览页的全局 CSS，因而不会修改整个 GitHub 页面背景、链接颜色或正文字体。

## 修改内容

| 文件 | 用途 |
| --- | --- |
| [`.profile/README.template.md`](../.profile/README.template.md) | 主页文案、导航、排版和文章占位符 |
| [`.profile/config.json`](../.profile/config.json) | 博客地址、RSS 地址和文章数量 |
| [`assets/hero.svg`](../assets/hero.svg) | 桌面纸色横幅 |
| [`assets/hero-mobile.svg`](../assets/hero-mobile.svg) | 手机专用横幅，在 600px 及以下切换 |
| [`scripts/update_profile.py`](../scripts/update_profile.py) | 获取 RSS、保留缓存并生成 README |
| [`.profile/cache.json`](../.profile/cache.json) | 上次成功获取的文章和内容更新时间 |

不要直接修改生成的 `README.md`。修改模板后运行：

```bash
# 获取最新文章并生成 README。
python3 scripts/update_profile.py

# 使用缓存重新生成，不联网。
python3 scripts/update_profile.py --offline

# 检查模板、缓存和生成结果是否一致。
python3 scripts/update_profile.py --check

# 验证 RSS/Atom 解析、故障回退、转义和幂等性。
python3 -m unittest discover -s tests -v
```

需要 Python 3.11+，仅使用标准库，不需要 npm、pip 或个人访问令牌。

## 每日同步

[Update profile](../.github/workflows/update-profile.yml) 每天北京时间 09:23 计划运行，也支持手动触发。修改主分支的模板、配置、横幅、脚本、测试或工作流时会自动运行。

- 从 `https://likeyy.love/rss.xml` 读取最新五篇文章，支持 RSS 2.0 和 Atom。
- 按发布时间倒序排列、按链接去重，日期使用北京时间；同一时间保持源顺序。
- 请求失败或响应无效时保留上次成功的文章，并在 Actions 日志和摘要里说明；首次运行没有缓存时失败退出。
- 只有文章内容变化才更新缓存时间并提交，避免每天产生无内容变化的提交。
- 使用 GitHub 内置令牌推送生成结果；仅更新任务具有仓库写入权限。并发排队，普通推送不会覆盖人工提交。
- GitHub 定时任务可能延迟；长期无活动时可在 Actions 页面重新启用。

## 展示说明

桌面与手机横幅均为仓库内的静态 SVG，使用系统字体，不依赖外部图片服务或远程字体。横幅附有替代文本；文章和订阅入口使用普通可访问链接，支持复制、搜索和键盘访问。

首页保留“我的仓库”入口，不再展示旧版统计卡片、项目卡片、工具墙和贡献动画，其生成代码、素材及工作流步骤已移除。
