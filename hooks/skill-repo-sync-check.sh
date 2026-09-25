#!/usr/bin/env bash
#
# 能力层仓库远程更新检测 —— Hermes cron 的 monitor 源脚本
#
# ⚠️ cron monitor 模式的硬性约束（违反会导致每小时空跑或告警刷屏）：
#   1. 输出必须确定性：同一远端状态下，两次运行的 stdout 必须逐字节相同。
#      禁止时间戳、禁止随机序、禁止把 `git status` 原文打出来。
#   2. 无输出 = 已是最新：monitor 哈希不变 → agent 完全不运行 → 零 token 成本。
#   3. 网络失败也要输出稳定内容：让 monitor 有明确基线。否则失败态每小时
#      都被判为"有变化"，agent 每轮空转；固定串让首轮之后即被抑制。
#   4. 本脚本只检测、不拉取：是否 pull、本地有没有未提交改动、冲突怎么处理，
#      需要 agent 判断，逻辑写在 cron 任务的 prompt 里。
#
# 安装位置：~/.hermes/scripts/skill-repo-sync-check.sh
# （Hermes cron 的 script / monitor_script 只接受 HERMES_HOME/scripts/ 内的路径）

set -u

REPO_DIR="${SKILL_REPO_DIR:-$HOME/skill-repo}"

# ─── 0. 仓库自检 ─────────────────────────────────────────────
# 仓库缺失是配置错误，必须走 stderr + 非零退出让 monitor 报错，
# 绝不能静默输出空串——那会让任务看起来"一直正常"却永不同步。
if [ ! -d "$REPO_DIR/.git" ]; then
    echo "能力层仓库不存在或不是 git 仓库: $REPO_DIR" >&2
    exit 1
fi
cd "$REPO_DIR" || exit 1

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
if [ -z "$BRANCH" ]; then
    echo "无法解析当前分支（$REPO_DIR）" >&2
    exit 1
fi

# ─── 1. 串行化 ───────────────────────────────────────────────
# post-commit hook 可能正在 push/pull，concurrent git 会撞 index.lock。
# 锁文件放 ~/.cache：绝不能放仓库内，未跟踪文件会让 git status 永久非空。
LOCK_DIR="$HOME/.cache/skill-repo"
mkdir -p "$LOCK_DIR" 2>/dev/null || true
exec 9>"$LOCK_DIR/sync.lock" 2>/dev/null || true
if command -v flock >/dev/null 2>&1; then
    flock -w 60 9 || { echo "GIT_BUSY"; exit 0; }
fi

# ─── 2. 代理探测 ─────────────────────────────────────────────
# 本机 IPv6 出口不通，github.com 必须走代理。gateway 进程环境里没有代理变量，
# 代理地址是设备特定的，写在 ~/.config/skill-repo/proxy.conf。
PROBE_SCRIPT="$REPO_DIR/.local/scripts/detect-proxy.sh"
[ -f "$PROBE_SCRIPT" ] || PROBE_SCRIPT="$HOME/.local/scripts/detect-proxy.sh"
if [ -f "$PROBE_SCRIPT" ]; then
    eval "$("$PROBE_SCRIPT" --export 2>/dev/null)" || true
fi

# ─── 3. 只更新 remote-tracking，不动工作区 ───────────────────
if ! git fetch --quiet origin "$BRANCH" 2>/dev/null; then
    # 稳定失败基线：agent 首轮得知后转 [SILENT]，之后哈希不变即被抑制，
    # 不会每小时重发同一条告警；网络恢复后输出变化，agent 自然被唤醒。
    echo "FETCH_FAILED"
    exit 0
fi

UPSTREAM="origin/$BRANCH"
if ! git rev-parse --verify --quiet "$UPSTREAM" >/dev/null; then
    echo "NO_UPSTREAM"
    exit 0
fi

# ─── 4. 计算落后提交 ─────────────────────────────────────────
# %h %s：短哈希 + 标题，无日期、无作者，跨设备稳定。
BEHIND="$(git log --no-decorate --format='%h %s' "HEAD..$UPSTREAM" 2>/dev/null)"

# 已是最新：无输出，monitor 哈希不变，agent 不运行。
[ -z "$BEHIND" ] && exit 0

# 本地未提交改动只输出布尔标志，不打 status 原文——
# 未跟踪文件增减会让输出抖动，把"无变化"误判成"有变化"。
LOCAL_DIRTY=0
[ -n "$(git status --porcelain 2>/dev/null)" ] && LOCAL_DIRTY=1

printf '待拉取提交（本地落后于 %s）：\n%s\n' "$UPSTREAM" "$BEHIND"
echo "LOCAL_DIRTY=$LOCAL_DIRTY"
exit 0
