# 主页维护说明

这是 GitHub 个人主页仓库。`README.md`、项目卡片、数据概览和工具图标卡片由 Python 标准库脚本生成；不需要 npm 或 pip 依赖。文章与仓库数据保存在版本控制中，GitHub 浏览者不需要调用这些 API。

## 修改内容

| 文件 | 用途 |
| --- | --- |
| [`.profile/README.template.md`](../.profile/README.template.md) | 主页文案、布局、导航和动态内容占位符 |
| [`.profile/config.json`](../.profile/config.json) | 精选项目、卡片介绍、工具列表和展示数量 |
| [`assets/hero.svg`](../assets/hero.svg) / [`hero-mobile.svg`](../assets/hero-mobile.svg) | 桌面动态封面与手机专用封面，支持减少动态效果偏好 |
| [`scripts/update_profile.py`](../scripts/update_profile.py) | 获取数据、处理缓存、渲染 README 和 SVG |
| [`.profile/cache.json`](../.profile/cache.json) | 最近一次成功获取的公开数据，供离线渲染和故障回退 |
| [`assets/icons/NOTICE.md`](../assets/icons/NOTICE.md) | Dashboard Icons 的来源、固定版本与许可说明 |

不要直接改生成的 `README.md` 或 `assets/generated/` 中的项目与工具卡片，下次同步会覆盖它们。修改模板或配置后运行：

```bash
# 从网络读取 GitHub 公开仓库与博客 RSS，并生成主页。
python3 scripts/update_profile.py

# 仅使用仓库内缓存，适合离线修改样式与文案。
python3 scripts/update_profile.py --offline

# 验证已生成文件与缓存、模板一致，不联网、不写文件。
python3 scripts/update_profile.py --check

# 网络故障回退、分页、RSS/Atom、转义与幂等性测试。
python3 -m unittest discover -s tests -v
```

需要 Python 3.11+（Actions 使用 3.13）。本地脚本不要求 Token；如遇 GitHub 匿名 API 限流，可通过环境变量 `GITHUB_TOKEN` 传入令牌，脚本不会把令牌写入缓存或输出。

## 自动更新

工作流：[`update-profile.yml`](../.github/workflows/update-profile.yml)。

- 每天北京时间 **09:23**（UTC `01:23`）计划运行，也可在 Actions → Update profile → Run workflow 手动运行。
- 修改 `main` 上的模板、配置、脚本、测试或工作流时自动运行。
- 使用内置 `github.token`，无需 `METRICS_TOKEN` 或其他个人令牌。写权限仅在更新任务中授予，用于提交生成内容。
- 工作流用固定提交 SHA 引用 Actions。首次将本次变更推送到 `main` 后，路径触发器会启动一次同步。
- 只有实际内容发生变化才提交。页脚时间表示「项目与文章内容最后变化时间」，不是每次运行时间；贡献动画单独刷新。
- 并发任务排队执行；如果运行期间有人工推送，普通 `git push` 会安全失败，不会覆盖人工提交。重新运行即可。

GitHub 的定时任务可能延迟；公开仓库连续 60 天没有活动时也可能被平台暂停，可从 Actions 页面重新启用。如果仓库规则禁止机器人直接写入 `main`，运行会在推送步骤明确失败，需要按仓库策略允许该自动提交或改用 PR 流程。

## 数据来源与筛选

- **数据概览**：GitHub 用户 API 的公开仓库总数（包含 fork）；卡片数量取自本地精选配置。
- **精选项目**：手动选择的六个代表作，项目名、介绍和技术栈由配置维护；星标、语言、推送日期每日从 GitHub 同步。
- **最近在写**：按 `pushed_at` 排序的前四个公开、非 fork、未归档、未禁用项目，排除主页仓库自身。
- **最新文章**：`https://likeyy.love/rss.xml`，按发布时间倒序、URL 去重，展示五篇；日期转换为北京时间，时间相同则保持源顺序。兼容 RSS 2.0 与 Atom。
- **贡献动画**：使用 [Platane/snk](https://github.com/Platane/snk) 的 SVG Action 读取公开贡献记录，以紫色蛇和绿色格子展示。

博客和 GitHub 分别回退：一个来源失败不阻止另一个来源更新。发生回退时，日志与 Actions 摘要会说明；不会清空已有内容。首次运行完全没有缓存且请求失败时，脚本报错退出。动画先写入 runner 临时目录，生成成功且 XML 有效后才替换仓库中的动画。

## 视觉与可访问性

GitHub README 不支持自定义页面 CSS、脚本或 iframe，因此采用 GitHub 支持的 Markdown、HTML、相对路径 SVG 和普通链接。封面、项目卡片与图标随仓库发布，避免依赖外部统计卡片服务。

- 封面和卡片使用固定深色底，在 GitHub 深色与浅色界面中保持一致。
- `picture` 在 600px 及以下切换手机封面与双行数据概览。项目卡片使用固定宽度配合 GitHub 自带的 `max-width`，窄屏自然换成单列，不依赖表格或自定义 CSS。
- 项目图像有描述性 `alt` 文本，并提供可展开的文字索引；文章保持普通文本链接，方便复制、搜索与屏幕阅读器访问。
- 中文依赖浏览设备的中文字体；GitHub 的 SVG 图片环境不适合加载远程字体。
- 工具图标来自 Dashboard Icons，保留上游源文件、许可和出处；浅色内衬保证黑色品牌图标可读。
- 原创封面尊重 `prefers-reduced-motion`；第三方贡献动画遵循上游 SVG 实现。
