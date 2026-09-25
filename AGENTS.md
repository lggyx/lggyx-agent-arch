# AGENTS.md

本文件是给**在本仓库工作的 AI Agent** 看的规则。人类读者请看 [README.md](README.md)。

---

## 这个仓库是什么

多 Agent 技能治理架构的机制层。用户拿到的第一件事是 clone 本仓库，然后按 README 的流程搭建属于自己的技能架构：

- 从零开始 → `scripts/bootstrap.py` 交互式向导
- 整理存量 → `scan.py` → `classify.py` → `relocate.py`

它是**机制**，不是**内容**：不放任何具体技能，只放让用户搭建和整理自己架构的工具与规则。

**如果用户是复制 [PROMPT.md](PROMPT.md) 里的 prompt 来找你的**，那段 prompt 已经把流程、边界、硬约束都写清楚了。按其执行，并遵守本文件的所有铁律。

---

## 铁律

### 1. 绝不替用户确认归属

`classify.py` 的预分类**一定会错**。你可以指出「这一项我判断可能是 third_party，因为检测到了 LICENSE.txt 里的 all rights reserved」，但**不能代替用户改 `review.json` 里的 `confirmed` 字段，也不能代替用户决定某个 skill 的归属**。

理由：归属判定错了代价很高。把别人的专有技能当原创开源是侵权；把官方自带技能迁进仓库，用户升级 Agent 后行为会不一致。这类决定必须由人做。

### 2. 不在未经显式授权时执行 `--apply`

`relocate.py` 默认 dry-run。你帮忙执行时：

- 先跑 dry-run，把输出**完整展示**给用户
- 等用户明确说「执行」再加 `--apply`
- 绝不因为「看起来没问题」就自行加 `--apply`

### 3. 不把私有信息写进仓库

以下内容**绝不提交**：

- 用户自己的私有仓库地址（`git remote` 对仓库内 skill 会返回同一个地址，那是用户仓库，不是上游来源——用 `sanitize_remote()` 过滤）
- 真实设备名、机器名、邮箱
- 本机 IP / 代理地址
- `inventory.json`、`review.json`（含用户本机完整路径与技能清单）
- `migration-log/`

`.gitignore` 已屏蔽这些，新增文件时确认没绕过它。

### 4. 推送前人工确认脱敏

本仓库是 public。推送前逐项过一遍：

- [ ] 无用户私有仓库地址
- [ ] 无真实设备名 / 邮箱 / IP
- [ ] 无具体技能内容（这是机制仓库，不是技能集合）
- [ ] 无 `inventory.json` / `review.json` 等含本机路径的产物
- [ ] 文档里的示例路径用的是 `/home/you/` 这类占位，不是真实用户名

---

## 代码约定

- **Python 3.10+，纯标准库**——不引 PyYAML 等第三方依赖。用户的机器上未必装了，而这个工具要在「刚装好 agent」的机器上就能跑。
- **中文注释，说明「为什么」而非「是什么」**——尤其是踩过的坑，要写清楚坑在哪，否则后人会再踩一遍。
- **新脚本必须配测试**，且在临时目录造 fixture，**绝不碰 `~/.hermes`、`~/skill-repo` 等真实位置**。
- **测试重点验证安全属性**，不只是功能正确。这些工具会移动/创建用户文件，「什么情况下拒绝执行」比「能执行」更重要。

已沉淀的坑（改代码前先读）：

| 坑 | 位置 |
|---|---|
| Hermes 的 skill 按 `skills/<分类>/<技能>/` 组织，不是平铺的——只扫第一层会漏掉大部分 | `scan.py: iter_skill_dirs()` |
| Agent 配置里的 `external_dirs` 是真源，最可靠的 personal 信号，但专有授权要压过它 | `classify.py: classify()` |
| 专有授权（`all rights reserved`）必须优先于 `external_dirs` 判定，否则会把 fork 的专有技能当原创开源 | `classify.py: classify()` |
| 路径匹配必须按 `/` 切段，不能用子串——`/tmp/agent-arch-test-xxx/` 会被误判成用户仓库 | `classify.py: path_has_segment()` |
| `classify()` 可能被单独调用，缺字段要给默认值而非抛 KeyError | `classify.py: classify()` |
| 预检必须先判「已在仓库内」，否则 dest == src 会误报「目标已存在」 | `relocate.py: plan_relocations()` |
| 日志目录要先建，`undo.sh` 要写进去 | `relocate.py: apply_plan()` |
| `git remote` 对仓库内 skill 返回的是用户私有仓库，不是上游来源 | `relocate.py: sanitize_remote()` |
| dry-run 下不能用「组件 1 是否建成」做后续组件的门槛，否则计划显示不完整 | `bootstrap.py: main()` |
| `--yes` / `--dry-run` 必须跳过所有提问，否则 CI 里会卡住 | `bootstrap.py: _yes()` |

---

## 修改流程

1. 改 `scripts/` 下的代码
2. 跑 `python3 -m unittest discover -s tests`，必须全绿
3. 用真实环境回归一次：`scan.py` → `classify.py` → `relocate.py`（dry-run，不加 `--apply`）
4. `bootstrap.py --yes` 预览一遍，确认 7 个组件计划完整
5. 若新增了 Agent 的 skill 位置，同步更新 `README.md` 的兼容性列表
6. 若踩到新坑，补进上面的表格
7. 提交前过一遍「脱敏检查清单」

---

## 仓库边界

本仓库**只放机制**：

```
scripts/     扫描、分类、迁移、引导工具
hooks/       远程更新检测脚本
tests/       单元测试
docs/        架构蓝图
PROMPT.md    给用户的 prompt 入口（复制即用）
README.md    人类文档
AGENTS.md    本文件
LICENSE      MIT
```

**不放**：具体技能、`inventory.json` / `review.json` 产物、`registry.yaml` 实例、任何设备特定配置。

用户的技能仓库由用户自己维护，与本仓库无关——本仓库的产物是「一套能跑的机制」，不是「一份技能清单」。
