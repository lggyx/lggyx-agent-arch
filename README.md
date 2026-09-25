# lggyx-agent-arch

多 Agent 技能治理架构。给 Agent 一套**可复刻的技能治理机制**。

它不做的事：教你写 skill、给你一套开箱即用的技能包。
它做的事：**把你本机散落各处的 skill 盘点清楚、分清归属、归一成单一真源，并让这份真源跨设备同步。**

---

## 怎么用

**你不用记任何命令。** 复制 README 里那段 prompt，发给你的 Agent，它会带着你走完。

---

## 解决什么问题

用 Agent CLI 一段时间后，典型状态是这样：

```
~/.hermes/skills/          62 个（按分类目录组织）
~/.claude/skills/           9 个（平铺）
~/.config/opencode/skills/  4 个（另一套）
~/Downloads/某个技能/       3 个（不知道什么时候复制的）
```

问题随之而来：

- **同一个技能装了 3 份**，改了一处，另外两处还是旧的
- **分不清哪些是自己写的、哪些是别人装的**——想开源或分享时不敢动手
- **换台设备就要重装一遍**，而且装完发现版本还不一样
- **不知道某个技能从哪来的**，原作者更新了无从跟进

本仓库把这件事流程化：**扫描 → 分类 → 人工确认 → 归一化 → 同步**。

---

## 快速开始

**复制 [PROMPT.md](PROMPT.md) 里那段话，发给你的 Agent。** 就这些。

简短版（完整版见 [PROMPT.md](PROMPT.md)）：

```text
帮我把本机的 skill 治理一下。

1. 先运行 `git clone https://github.com/lggyx/lggyx-agent-arch.git ~/agent-arch`
2. 读 ~/agent-arch/README.md 和 AGENTS.md，按「三步流程」带我走一遍
3. 第一步跑 `python3 ~/agent-arch/scripts/scan.py -o ~/agent-arch/inventory.json`，
   把盘点结果讲给我听：本机有哪些 skill、有没有重名的、哪些缺 SKILL.md
4. 第二步跑 `python3 ~/agent-arch/scripts/classify.py` 生成分类清单，
   然后**逐项问我归属**（个人原创 / 第三方 / 官方自带），
   不要替我做决定，也不要擅自动文件
5. 我确认完之后，先跑 dry-run 给我看，我说"执行"你才能加 --apply
6. 过程中凡是涉及删除、移动、覆盖的操作，一律先问我
```

Agent 会自己读文档、跑脚本、把结果解释给你听。

**两个关键点**：

- 这个工具**会移动你的文件**，所以第 5、6 条是硬约束。你的 Agent 应该遵守——如果不遵守，换个 Agent。
- 想要的是"从零建一套架构"而不是"整理现有的"？PROMPT.md 里另有对应的一段。

---

## 命令行用法（进阶）

想自己控制每一步，或者写进 CI：

```bash
git clone https://github.com/lggyx/lggyx-agent-arch.git
cd lggyx-agent-arch

python3 scripts/bootstrap.py            # 交互式搭建架构骨架
python3 scripts/scan.py -o inventory.json        # 只读盘点
python3 scripts/classify.py inventory.json -o review.json
$EDITOR review.json                     # 给要处理的项加 "confirmed": true
python3 scripts/relocate.py review.json --repo ~/skill-repo          # dry-run
python3 scripts/relocate.py review.json --repo ~/skill-repo --apply  # 执行
```

`relocate.py` **不加 `--apply` 绝不改动任何文件**。

---

## 两个入口

| 你的情况 |  prompt 里怎么说 | 对应脚本 |
|---|---|---|
| 刚接触，想从零建一套 | 「用 bootstrap 带我搭一套技能架构」 | `bootstrap.py` |
| 已经装了一堆 skill，想管起来 | 「按 README 三步流程治理我的 skill」 | `scan` → `classify` → `relocate` |

完整架构蓝图（7 个组件、依赖顺序、最小可用版本）见 [docs/架构蓝图.md](docs/架构蓝图.md)。

---

## 三步流程

> 这三步由你的 **Agent** 执行，你负责看结果、做归属决策。
> 下面写清楚每步在做什么、为什么——既给你看懂，也给 Agent 读。

### 第一步：扫描（scan.py）

只读。遍历所有已知 Agent 的 skill 位置，抓每个 skill 的 frontmatter、软链状态、体积、内容指纹。

**为什么需要它**：不同 Agent 的组织方式不一样——Hermes 按 `skills/<分类>/<技能>/` 组织，Claude Code 是平铺的，还有的技能通过配置里的 `external_dirs` 加载。不先扫一遍，你根本不知道本机到底有多少、在哪。

同时它会标出两类问题：

- **重名技能**：按 frontmatter name 分组，并区分「内容完全相同」（真重复）与「版本已分叉」（要决定留哪份）
- **缺 SKILL.md**：不符合 [agentskills.io](https://agentskills.io) 规范的目录

```bash
python3 scripts/scan.py -o inventory.json
```

### 第二步：预分类（classify.py）

启发式判断每个 skill 属于哪类，**只输出清单，不改文件**。

| 分类 | 含义 | 处理方式 |
|---|---|---|
| `official` | Agent 官方自带 | 保持原样（升级 Agent 时会更新，迁走反而坏事） |
| `third_party` | 从别处安装的 | 登记来源 URL + commit hash，**不迁内容** |
| `personal` | 你自己写的 | 迁入仓库真源，各处软链指回去 |
| `unknown` | 信号不足 | 必须人工判断 |

判定信号按可靠性排序：

1. **`external_dirs`**（最可靠）——你主动在配置里指定的目录，就是你的真源
2. **专有授权声明**——`LICENSE.txt` 里出现 `all rights reserved` / `proprietary` 等
3. **第三方声明文件**——`THIRD_PARTY_NOTICES.md` / `NOTICE`
4. **内嵌 `.git`**——直接 clone 下来的
5. **文档里的上游链接**——出现别人的 GitHub 仓库地址
6. **软链状态**——已经是软链说明已被治理过

**一个关键设计**：专有授权信号的优先级**高于** `external_dirs`。

很多人会把自己 fork 的技能放进 `external_dirs`。若因为「在 external_dirs 里」就判为 `personal`，用户会顺手把别人的专有内容当原创开源——那是侵权。所以第 2 类信号必须压过第 1 类。

```bash
python3 scripts/classify.py inventory.json -o review.json
```

### 第三步：人工确认 + 归一化（relocate.py）

分类是启发式的，**一定会错**。所以必须人工过一遍清单：

```bash
$EDITOR review.json    # 给要处理的项加 "confirmed": true
```

然后先 dry-run：

```bash
python3 scripts/relocate.py review.json --repo ~/skill-repo
```

输出会长这样：

```
【将迁入真源】 /home/you/skill-repo/skills/  (3 个)
  → my-writer
      /home/you/.hermes/skills/creative/my-writer
      ⇒ /home/you/skill-repo/skills/my-writer（原位置建软链）
  ✗ another-skill: 目标已存在（不会覆盖）

【将登记到 registry.yaml】 (2 个，不动内容)
  · forked-thing
      来源: https://github.com/someone/forked-thing
      版本: a1b2c3d4e5f6
```

确认后加 `--apply` 执行。执行时：

- 移动目录到仓库 `skills/` 真源，**原位置建软链指回去**——Agent 照常加载，但内容只有一份
- 第三方技能写进 `registry.yaml`（来源 + 版本），内容原地不动
- 每次操作记入 `migration-log/`，并生成 `undo.sh` 回滚脚本
- **软链创建失败会自动回滚移动**，不会让技能「消失」

---

## 归一化之后

```
~/skill-repo/            # 你的私有仓库（git）
├── skills/                # 唯一真源
│   ├── my-writer/         # 你写的
│   └── my-charts/
├── registry.yaml          # 第三方登记（来源 + 版本）
├── AGENTS.md              # 治理规则
└── hooks/
```

在各 Agent 配置里指向它：

```yaml
# ~/.hermes/config.yaml
skills:
  external_dirs:
    - /home/you/skill-repo/skills
```

之后**所有修改都只发生在真源**，`git commit` 一次，别的设备 `git pull` 就同步了。

---

## 目录规矩

- `~` 根目录只放**系统级仓库**（本仓库、你的 skill-repo、dotfiles）
- **具体项目进 `~/work/<项目名>/`**，不要往 `~` 根目录堆
- Agent 运行时状态（`.hermes/`、`.claude/`）不算仓库，各自的配置各自维护

---

## 安全设计

这个工具会移动你的文件，所以：

| 机制 | 作用 |
|---|---|
| scan / classify 只读 | 前两步绝无副作用，可以放心反复跑 |
| relocate 默认 dry-run | 不加 `--apply` 不碰文件 |
| 必须显式 `confirmed: true` | 防止误执行；没确认的项一律跳过 |
| 目标已存在即中止 | 绝不静默覆盖你可能已整理好的内容 |
| 软链失败自动回滚 | 不会让技能「消失」 |
| `migration-log/` + `undo.sh` | 每次操作可追溯、可撤销 |
| 专有内容保护 | 检测到专有授权不迁移、不开源 |

### 脱敏：不要把私有地址写进 registry

实测踩到的坑：如果你的 skill 都在一个 git 仓库里（比如你整个技能仓库），`git remote get-url` 对**每个** skill 都会返回同一个地址——那是**你自己的私有仓库**，不是上游来源。

`relocate.py` 的 `sanitize_remote()` 会过滤这种情况：只有 skill 位于仓库根的 `skills/<name>` 下，就判定 remote 是用户自己的仓库，不当上游来源写入。

---

## 测试

```bash
python3 -m unittest discover -s tests -v
```

28 个用例，全部在临时目录里造 fixture，**不碰 `~/.hermes`、`~/skill-repo` 等真实位置**。

测试重点不是「能迁移」，而是「什么情况下拒绝迁移」：

- 专有授权必须压过 `external_dirs` 信号（防侵权）
- 未确认的项不进入计划
- 目标已存在必须报错
- 软链失败必须回滚
- 私有仓库地址不能当上游来源写入
- 路径段匹配不能被子串误伤（`/tmp/agent-arch-test-xxx/` 不该被当成用户仓库）
- dry-run 下所有组件都必须出现在计划里（不能因组件 1 未建成就静默跳过后续）
- 模板文件不能含编造内容（设备名/日期/资源名都不行）

---

## 兼容性

- Python 3.10+，**无第三方依赖**（纯标准库，不引 PyYAML）
- 已知 Agent：Hermes、Claude Code、OpenCode、pi、Cursor、Codex、Gemini、Goose
- 未覆盖的 Agent 可在 `scan.py` 的 `AGENTS_SKILL_DIRS` / `AGENT_CONFIG_FILES` 里追加

---

## 设计取舍

**为什么分类必须人工确认**：归属判定错了代价很高——把别人的专有技能当原创开源是侵权，把官方技能迁走会导致升级后行为不一致。自动化图省事，出错了用户不一定发现。所以这里有意保留摩擦。

**为什么不迁第三方内容**：迁进来就有版本漂移（原作者更新了你不知道），还可能有授权问题。只登记来源 + commit hash，需要时按记录去取。

**为什么真源用软链而不是复制**：复制就会出现「改了一处忘了另一处」，这正是要解决的问题。软链让加载路径不变，但内容只有一份。

---

## 许可

MIT
