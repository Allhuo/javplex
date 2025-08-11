# JAV Metadata Updater for Plex

🎬 自动为 Plex 媒体服务器中的 JAV 视频添加中文分类和元数据的 Python 工具

## 功能特点

✨ **智能番号提取** - 支持多种文件命名格式和复杂路径结构  
🌏 **中文元数据** - 直接获取 JavLibrary 中文版内容，无需额外翻译  
🛡️ **CloudFlare 绕过** - 内置反爬虫机制，稳定访问 JavLibrary  
🚀 **批量处理** - 支持多线程并发处理，提高效率  
⚡ **智能频率限制** - 防止被封禁的访问频率控制和重试机制  
🎯 **精确映射** - 自动添加中文类别标签和合集分组  
🔧 **灵活配置** - 通过 YAML 配置文件自定义各种参数

## 工作原理

1. **连接 Plex** - 通过 API 获取媒体库中的视频文件
2. **提取番号** - 从文件名/路径中智能识别 JAV 番号（如 CJOD-160、AP-514 等）
3. **爬取元数据** - 从 JavLibrary 中文版获取详细信息
4. **更新 Plex** - 自动添加中文类别、演员标签、合集分组等

## 安装和配置

### 1. 系统要求

- Python 3.7+
- Plex Media Server
- 网络访问 JavLibrary

### 2. 安装依赖

```bash
# 克隆或下载项目到本地
cd jav-meta

# 创建虚拟环境（推荐）
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# 或 venv\Scripts\activate  # Windows

# 安装依赖包
pip install -r requirements.txt
```

### 3. 配置设置

复制并编辑配置文件：

```bash
cp config.yaml.example config.yaml  # 如果有示例文件
# 或直接编辑 config.yaml
```

**重要配置项：**

```yaml
# Plex 服务器设置
plex:
  url: "http://你的Plex服务器IP:32400"
  token: "你的Plex令牌"  # 在 Plex 设置 > 网络 > 显示高级设置中获取
  library: "你的JAV视频库名称"

# JavLibrary 爬虫设置
javlibrary:
  language: "cn"  # 使用中文版
  rate_limit: 2.0  # 请求间隔2秒，防止被封
  # 如遇到 403 错误，需要添加浏览器 cookies：
  cookies: "你的浏览器cookies"
```

### 4. 获取 Plex Token

1. 登录 Plex Web 界面
2. 进入 `设置 > 网络 > 显示高级设置`
3. 找到并复制 `X-Plex-Token` 值

### 5. 获取 CloudFlare Cookies（可选）

如果遇到 403 错误：

1. 用浏览器访问 JavLibrary 并登录
2. 按 F12 打开开发者工具
3. 刷新页面，在 Network 标签中找到任意请求
4. 复制 Request Headers 中的完整 Cookie 值
5. 将其添加到 `config.yaml` 的 `cookies` 字段

## 使用方法

### 基本用法

```bash
# 测试连接（推荐首次运行）
python test_plex.py

# 测试模式 - 只获取元数据，不更新 Plex
python jav_meta_updater.py --dry-run --limit 5

# 正式运行 - 处理前10个视频
python jav_meta_updater.py --limit 10

# 处理特定番号
python jav_meta_updater.py --code CJOD-160

# 完整处理所有视频
python jav_meta_updater.py
```

### 高级选项

```bash
# 自定义配置文件
python jav_meta_updater.py --config my_config.yaml

# 调整并发线程数
python jav_meta_updater.py --threads 5

# 查看帮助
python jav_meta_updater.py --help
```

## 支持的文件格式

工具可以从以下格式的文件名中提取番号：

```
✅ 标准格式：CJOD-160.mp4
✅ 带标题：AP-514 图书馆夫妇戴绿帽子.mp4  
✅ 复杂路径：/path/MOON-027 只要是乳交就不算/video.mp4
✅ 各种分隔符：ABC_123.avi, DEF.456.mkv
✅ 特殊格式：1PON-123, 012345-123
```

## 自动分类效果

运行后，你的 Plex 库将自动获得：

### 🏷️ 中文类别标签
- 人妻、熟女、美少女
- 制服、护士、教师、OL
- 素人、出道作品、VR
- 纪录片、剧情、高清等

### 📁 智能合集分组
- **人妻熟女** - 包含人妻、熟女类别
- **制服系列** - 包含制服、护士、教师等
- **素人作品** - 包含素人类别
- **角色扮演** - 包含cosplay相关
- **新人出道** - 包含出道作品

### 👤 演员标签
- 自动添加前5名演员作为标签
- 支持日文和中文演员名

### 📊 其他元数据
- 制作商信息
- 发行日期
- 评分信息
- 番号标签

## 配置详解

### 类别映射

你可以在 `config.yaml` 中自定义类别翻译：

```yaml
genre_mapping:
  "Mature Woman": "熟女"
  "Uniform": "制服"
  "School Girls": "女学生"
  "Cosplay": "角色扮演"
  # 添加更多映射...
```

### 合集映射

设置自动合集分组：

```yaml
collection_mapping:
  "人妻": "人妻熟女"
  "熟女": "人妻熟女"
  "制服": "制服系列"
  "素人": "素人作品"
  # 添加更多合集...
```

### 高级设置

```yaml
javlibrary:
  rate_limit: 2.0      # 请求间隔（秒）
  max_retries: 3       # 最大重试次数
  timeout: 10          # 请求超时（秒）

rules:
  skip_with_genres: false        # 跳过已有类别的视频
  overwrite_title: false         # 覆盖现有标题
  max_actors_as_tags: 5          # 最多添加几个演员标签
```

## 故障排除

### 常见问题

**Q: 提示 "Connection refused" 错误？**  
A: 检查 Plex 服务器地址和端口，确保服务器运行中且网络可达。

**Q: 遇到 403 Forbidden 错误？**  
A: 需要配置 CloudFlare cookies，参考上面的获取方法。

**Q: 找不到视频库？**  
A: 运行 `python test_plex.py` 查看可用的库名称，更新配置文件。

**Q: 提取不到番号？**  
A: 检查文件命名格式，支持的格式见上面列表。可以运行测试脚本检查：

```bash
python -c "from jav_meta_updater import JAVNumberExtractor; print(JAVNumberExtractor.extract('你的文件名.mp4'))"
```

**Q: 被 JavLibrary 封禁？**  
A: 增加 `rate_limit` 值（如改为 3.0），减少并发线程数。

### 调试模式

启用详细日志：

```yaml
# 在 config.yaml 中设置
advanced:
  log_level: "DEBUG"
```

或设置环境变量：

```bash
export LOG_LEVEL=DEBUG
python jav_meta_updater.py --dry-run
```

## 文件结构

```
jav-meta/
├── jav_meta_updater.py    # 主程序
├── test_plex.py           # 连接测试脚本
├── config.yaml            # 配置文件
├── requirements.txt       # Python依赖
├── README.md             # 说明文档
├── .gitignore            # Git忽略文件
└── logs/                 # 日志目录
    └── jav_meta_updater.log
```

## 更新日志

### v1.0.0
- ✅ 支持智能番号提取
- ✅ JavLibrary 中文版集成
- ✅ CloudFlare 绕过机制
- ✅ 批量处理和多线程
- ✅ 智能频率限制
- ✅ 完整的配置系统

## 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 此项目
2. 创建特性分支：`git checkout -b feature/amazing-feature`
3. 提交更改：`git commit -m 'Add amazing feature'`
4. 推送分支：`git push origin feature/amazing-feature`
5. 提交 Pull Request

## 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 免责声明

⚠️ **重要提醒**

- 本工具仅用于个人学习和研究目的
- 请遵守当地法律法规和网站服务条款
- 使用前请确保拥有视频文件的合法权限
- 作者不承担任何使用本工具产生的法律责任

## 支持项目

如果这个项目对你有帮助，请给个 ⭐ Star！

---

💡 **提示**: 首次使用建议先用 `--dry-run --limit 5` 测试效果，确认无误后再全量处理。