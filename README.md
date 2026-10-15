# xiaogpt-plus

让小爱音箱：

- **日常对话交给千问**（阿里云 DashScope）。设备控制、天气、闹钟等仍由小爱自己处理。
- **播放 NAS 上的本地音乐**：按歌名、歌手模糊匹配，支持暂停、继续、上一首、下一首、停止，可以收藏歌曲并循环播放收藏。

可以直接用 Python 运行，也可以在 NAS 上用 Docker 运行（见下文「Docker 部署」）。架构说明见 [docs/architecture.md](docs/architecture.md)。

## 部署

需要 Python 3.9–3.12，运行机器和音箱在同一局域网。用 Docker 的话直接看下文「Docker 部署」。

```shell
pip install -r requirements.txt
cp xiao_config.yaml.example xiao_config.yaml
```

编辑 `xiao_config.yaml`，至少填：

- `account` / `password`：小米账号
- `hardware` / `mi_did`：音箱型号和 miotDID（见下文）
- `qwen_key`：千问 API key，也可用环境变量 `DASHSCOPE_API_KEY`
- `music_dir`：音乐目录，NAS 上指向映射进来的目录
- `music_http_host`：音箱能访问到的本机局域网 IP。留空会自动探测；在容器或多网卡环境里建议手动填

启动：

```shell
python xiaogpt.py
```

不带参数时自动读取 `xiaogpt.py` 同目录下的 `xiao_config.yaml`；要用别的配置文件就加 `--config 路径`。配置文件里的相对路径（如 `music_dir: "./music"`）按配置文件所在目录计算，从哪个目录启动都一样。

音箱通过 `http://{music_http_host}:{music_http_port}/song/<id>?t=<口令>` 从本机拉取音频。地址里的口令按日期每天更换，`music_http_token` 只是种子；留空则本次运行自动生成种子。没有当天口令的请求会被拒绝。跨午夜还在播放的歌，昨天的口令仍然有效。记得放行 `music_http_port`（默认 9528）。

## Docker 部署

在 NAS 上部署就是三步：把项目文件夹放进一个目录、准备好 `data/`、执行 `./run.sh`。镜像里只有 Python 和依赖，代码不打进镜像。启动时把整个项目目录挂到容器的 `/app`，所以 `data/` 里的配置、登录凭证和收藏跟着代码一起进去；音乐目录另外挂到 `/music`。改代码不用重新构建，重启容器即可。只有 `requirements.txt` 变了才需要重建镜像。

NAS 和音箱要在同一局域网，NAS 上要先装好 Docker。

1. 把整个项目文件夹拷到 NAS，例如 `/volume1/docker/xiaogpt-plus`，用 SSH 进入这个目录。`data/` 要和 `Dockerfile`、`run.sh` 放在一起：

   ```text
   xiaogpt-plus/
   ├── Dockerfile
   ├── run.sh
   ├── xiaogpt.py
   └── data/
       ├── xiao_config.yaml
       └── .mi.token
   ```

2. 准备 `data/`。已经在本机调好配置的话，直接拷过去：

   ```shell
   mkdir -p data
   cp xiao_config.yaml data/xiao_config.yaml
   cp ~/.mi.token data/.mi.token
   ```

   `cp ~/.mi.token` 在已经登录过小米的电脑上执行，再把 `data/.mi.token` 一起拷到 NAS，这样不用重新做安全验证。配置里的 `mi_device_id` 不要改。

   还没有配置文件就用示例：`cp xiao_config.yaml.example data/xiao_config.yaml`，再按上文「部署」里的项目填写。

   `data/xiao_config.yaml` 里把 `music_http_host` 改成 NAS 的局域网 IP（如 `192.168.1.10`）。容器里自动探测拿不到正确地址，音箱会到错误的机器上去拉歌。`music_dir` 不用改，容器里固定是 `/music`。

3. 打开 `run.sh`，把开头的 `MUSIC_DIR` 改成 NAS 上放歌的真实目录。群晖常见是 `/volume1/music`。

4. 构建镜像并启动：

   ```shell
   chmod +x run.sh
   ./run.sh
   ```

   第一次执行会构建镜像 `xiaogpt-plus`（里面只有依赖），再启动名为 `xiaogpt` 的容器，并挂上两个目录：

   | 宿主机 | 容器内 | 作用 |
   | --- | --- | --- |
   | 项目目录 | `/app` | 代码，以及其中的 `data/`（配置、登录凭证、收藏） |
   | `run.sh` 里的 `MUSIC_DIR` | `/music`（只读） | 本地音乐 |

   项目目录要挂进去：启动参数写的是 `python xiaogpt.py --config /app/data/xiao_config.yaml`，镜像里没有代码，也没有配置。

   启动后浏览器打开 `http://NAS的IP:9530`（设了 `web_password` 才会有这个页面）。看日志：

   ```shell
   docker logs -f xiaogpt
   ```

   `Ctrl+C` 只退出日志，容器继续跑。

常用命令：

| 操作 | 命令 |
| --- | --- |
| 停止 | `docker stop xiaogpt` |
| 启动 | `docker start xiaogpt` |
| 重启 | `docker restart xiaogpt` |
| 改了代码 | `docker restart xiaogpt` |
| 改了 `requirements.txt` | `./run.sh --build` |

说明：

- `9528` 只绑在 `run.sh` 里的 `LAN_IP` 上，给音箱在局域网拉歌，不监听公网地址。`9530` 是管理页面。`docker ps` 里音乐端口应显示为 `局域网IP:9528->9528`，而不是 `0.0.0.0:9528`。改了配置里的端口时，`run.sh` 里的 `-p` 要一起改，然后重新执行 `./run.sh`。
- `music_http_host` 仍填 NAS 的局域网 IP。容器自己探测到的是内网地址，音箱访问不到。
- 构建时 pip 默认用阿里云镜像。换源可以改 `run.sh` 里的构建命令，加上 `--build-arg PIP_INDEX_URL=https://pypi.org/simple`。

## 管理页面

在配置里设置 `web_password` 后，启动时会同时打开管理页面 `http://本机IP:9530`（端口改 `web_port`）。打开后先输入这个密码，可以：

- 查看运行状态：是否连上音箱、千问模型、曲库数量、正在播放什么；
- 登录小米：需要安全验证时页面直接给出验证链接，验证完点「我已完成验证，重新登录」；登录成功后点选音箱，自动写入 `hardware` 和 `mi_did`；
- 修改常用设置并保存回 `xiao_config.yaml`（保留文件里的注释）。千问、对话、轮询（查询间隔、闲置降频、夜间低频）相关的改动立即生效，小米和音乐相关的会自动重新连接；
- 测试：问千问、让音箱说一句话、搜索并播放本地音乐、暂停 / 继续 / 切歌。

开启管理页面后，配置不全或小米登录失败时程序不会退出，而是等你在页面上修好。不设 `web_password` 则不启动页面，行为和以前一样。管理密码只能在配置文件里改，改完重启生效。

密码输错后要等一段时间才能再试，等待时间逐次翻倍（5、10、20、40、80、160 秒）；当天累计输错 7 次，管理页面停用到第二天 0 点。计数只保存在内存里，重启程序会清零。

## 获取设备型号和 DID

```shell
python list_mi_devices.py --config xiao_config.yaml
# 或
python list_mi_devices.py --account ${account} --password ${password}
```

把输出里的 `hardware` 和 `miotDID` 填进配置。

### 首次登录的安全验证

小米按登录设备号（`mi_device_id`）识别设备，新设备第一次登录会要求安全验证。这时脚本会打印一个验证链接：在浏览器打开、完成短信验证后，重新运行即可。

验证通过后，登录凭证保存在 `mi_token_path`（默认 `~/.mi.token`）。部署到服务器时：

- 保持配置里的 `mi_device_id` 不变；
- 把本机的 `~/.mi.token` 一起拷到服务器同一位置（或改 `mi_token_path` 指过去），服务器启动时直接用凭证登录，不用再输密码验证。

服务器出口 IP 和本机差别大时，小米仍可能再要求一次验证，按打印的链接再做一次即可。

## 怎么说

| 说法 | 效果 |
| --- | --- |
| 播放晴天 / 我想听周杰伦 / 放周杰伦的歌 / 来一首稻香 | 在本地曲库模糊匹配并播放，按匹配度排成队列 |
| 播放音乐 / 随便来一首 / 打开本地音乐 | 打乱整个曲库循环播放 |
| 暂停 / 继续 / 下一首 / 上一首 / 停止 | 控制本地音乐 |
| 收藏这首歌 / 我喜欢这首歌 / 加入收藏 | 把正在放的歌加入收藏，说完接着放 |
| 取消收藏 / 不喜欢这首歌 / 移出收藏 | 把正在放的歌移出收藏 |
| 播放我的收藏 / 播放我喜欢的歌 | 打乱收藏循环播放 |
| 开灯 / 空调调到 26 度 / 今天天气怎么样 / 定个闹钟 | 交给小爱处理（设备名 + 开关调节动作，或命中 `xiaoai_keywords`；「电视剧」这类只提到设备名的话仍交给千问） |
| 其他任何话 | 千问回答，用小爱 TTS 播报 |
| 开始持续对话 / 结束持续对话 | 进入或退出连续对话（回答完自动唤醒） |
| 更改提示词你是一只猫 | 修改千问的系统提示词 |

本地找不到歌时会回复「本地没有找到…」，不会退回小米音乐。

## 配置项

参数优先级：命令行 > 配置文件 > 默认值。

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| hardware | 音箱型号 | `LX06` |
| account / password | 小米账号密码，也可用环境变量 `MI_USER` / `MI_PASS` | |
| cookie | 小米 cookie，填了可不用账号密码 | |
| mi_did | 设备 miotDID，也可用环境变量 `MI_DID` | |
| mi_device_id | 登录用的固定设备号，换机器部署时保持不变，也可用环境变量 `MI_DEVICE_ID` | 留空自动生成 |
| mi_token_path | 小米登录凭证文件 | `~/.mi.token` |
| web_password | 管理页面密码，留空不启动管理页面 | |
| web_port | 管理页面端口 | `9530` |
| qwen_key | 千问 API key，也可用环境变量 `DASHSCOPE_API_KEY` | |
| qwen_base_url | 百炼 OpenAI 兼容接口 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| qwen_options | 传给接口的参数，如 `model`、`temperature`、`enable_thinking` | `model: qwen-turbo`，`enable_thinking: false` |
| prompt | 千问系统提示词 | 智能音箱助手，简洁口语化 |
| music_dir | 本地音乐目录 | `./music` |
| favorites_path | 收藏列表文件，相对路径按配置文件目录计算（Docker 里落在 `data/`） | `favorites.json` |
| music_http_host | 音箱访问本机用的 IP，留空自动探测（也可用环境变量 `XIAOGPT_HOSTNAME`） | |
| music_http_port | 音乐 HTTP 端口 | `9528` |
| music_http_token | 口令种子，不是地址里的口令。地址里的口令按日期每天换一个。留空则本次运行自动生成种子。歌单接口仍要先登录管理页面 | |
| xiaoai_keywords | 交给小爱处理的关键词，包含即命中（设备控制另有内置规则，不用写进来） | 打开、关闭、音量、闹钟、提醒、几点、天气等 |
| mute_xiaoai | 接管时打断小爱自己的回答 | `true` |
| takeover_filler | 拿到问题后立刻用小爱原声说的垫话，顶掉小爱抢说的半句；越短越好，留空不说 | `思考中` |
| mute_window | 拿到问题后持续压制小爱的秒数，期间小爱一开口就暂停；还抢话就调大 | `1.5` |
| poll_interval | 多久查一次音箱对话记录（秒），越小打断越快 | `0.5` |
| idle_after | 多少秒没人说话就降低查询频率，有新对话立刻恢复 | `180` |
| idle_poll_interval | 闲置时多久查一次对话记录（秒）；闲置后的第一句会晚一点被发现 | `1.5` |
| night_start / night_end | 夜间低频时段（几点到几点，可跨午夜；相同表示不启用） | `0` / `7` |
| night_poll_interval | 夜间多久查一次对话记录（秒） | `2` |
| stream | 千问流式回答 | `true` |
| use_command | 用 MIoT command 播报（LX04、X10A、L05B、L05C 等需要） | `false` |
| tts | `mi`（小爱原声）或第三方：`edge`、`openai`、`azure`、`google`、`baidu`、`volc`、`minimax`、`fish` | `mi` |
| tts_options | 第三方 TTS 参数，见 [tetos](https://github.com/frostming/tetos) | |
| start_conversation / end_conversation | 持续对话的开始 / 结束口令 | `开始持续对话` / `结束持续对话` |
| change_prompt_keyword | 修改提示词的口令前缀 | `["更改提示词"]` |
| verbose | 详细日志，命令行 `-v` 可叠加 | `0` |

## 音频格式

扫描 `mp3`、`m4a`、`aac`、`flac`、`wav`、`ogg`。音箱对 mp3 支持最好；flac 等格式能否播放取决于型号，不行的话需要预先转成 mp3。

歌曲信息优先读标签（mutagen），标签缺失或是 `tmp`、`untitled`、`track 01` 这类无效值时，从文件名 `歌手 - 歌名` 解析。新加的歌在点不到时会自动重新扫描（距上次扫描超过 60 秒）。

## 注意

1. 部分型号（LX04、X10A、L05B、L05C）需要 `use_command: true`，且只支持小爱原声 TTS。
2. 海外登录大陆小米账号可能被风控，可改用 cookie，或把本机 `~/.mi.token` 拷到运行环境的用户 home 目录。见 [issue 332](https://github.com/yihong0618/xiaogpt/issues/332)。
3. 小米云仍负责登录、拾音和下发播放命令，断网时不能用。

## 感谢

- 上游项目 [yihong0618/xiaogpt](https://github.com/yihong0618/xiaogpt)
- [Tetos](https://github.com/frostming/tetos)
- @[Yonsm](https://github.com/Yonsm) 的 [MiService](https://github.com/Yonsm/MiService)
