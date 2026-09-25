# 复制这段，发给你的 Agent

> **用法**：整段复制，粘贴给你正在用的 Agent（Hermes / Claude Code / OpenCode / pi 等），
> 发送。然后按它的提示回答归属问题即可。
>
> 整个过程中，涉及移动、删除、覆盖你文件的操作，它都必须先问你。

---

```text
帮我把本机的 skill 治理一下，按 lggyx-agent-arch 的流程来。

第 0 步：先克隆仓库
  git clone https://github.com/lggyx/lggyx-agent-arch.git ~/agent-arch

第 1 步：读文档
  读 ~/agent-arch/README.md 的「三步流程」，以及 ~/agent-arch/AGENTS.md。
  AGENTS.md 里的铁律对本次任务全程有效。

第 2 步：盘点（只读，不要改任何文件）
  python3 ~/agent-arch/scripts/scan.py -o ~/agent-arch/inventory.json
  然后把结果讲给我听，重点说清：
  - 本机一共有多少个 skill，分布在哪些位置
  - 有没有重名的（哪些内容完全相同、哪些版本已经分叉）
  - 哪些目录缺 SKILL.md
  - 哪些是软链（说明已经被治理过）

第 3 步：预分类（只读，不要改任何文件）
  python3 ~/agent-arch/scripts/classify.py \
      ~/agent-arch/inventory.json -o ~/agent-arch/review.json
  然后**逐项问我归属**，分类是这四种：
  - personal     我自己写的      → 之后迁入真源
  - third_party  别人那拿来的    → 之后只登记来源，不迁内容
  - official     Agent 自带的    → 保持原样
  - unknown      你判断不了的   → 更要问我，不要猜

  规则：
  - 每项都要告诉我「你为什么这么判」（比如检测到了 LICENSE.txt 里的
    all rights reserved），但我来最终决定
  - 不要替我改 review.json 里的 category，也不要擅自加 confirmed: true
  - 遇到专有授权声明（proprietary / all rights reserved）要特别提醒我，
    这类不能当原创开源

第 4 步：执行前必须先给我看
  我确认完归属之后，先跑 dry-run：
    python3 ~/agent-arch/scripts/relocate.py \
        ~/agent-arch/review.json --repo ~/skill-repo
  把完整输出给我看。我说"执行"之后，你才能加 --apply。
  我不说"执行"，你就一直停在 dry-run。

全程硬约束：
  - 不删、不移、不覆盖任何文件，除非我明确说了"执行"
  - 目标位置已存在就停下报错，不要覆盖
  - 发现不确定的情况（比如软链指向不存在的位置、技能名和
    frontmatter 里的 name 不一致），停下来问我，不要自己推断
  - 不要把 ~/agent-arch/inventory.json 或 review.json 提交到任何 git 仓库，
    这两个文件含我本机的完整路径
```

---

## 如果你想要的是「从零建一套」

把上面第 2、3、4 步替换成：

```text
第 2 步：引导搭建
  python3 ~/agent-arch/scripts/bootstrap.py
  它是交互式的，会逐个组件问我"要不要建"（一共 7 个组件：
  唯一真源、索引技能、上下文层、同步机制、占用登记、治理规则、装机流程）。
  每一步都让我决定，不要替我选，也不要一次全建完。

第 3 步：接入 Agent
  按脚本最后的提示，把 external_dirs 配到我的 Agent 配置里。
  改配置文件前先给我看要改什么。
```

---

## 如果 Agent 不遵守约束怎么办

上面 prompt 里的「全程硬约束」是给 Agent 的边界。如果它跳过 dry-run 直接
`--apply`、或者不问你归属就自行分类，**停下来，换个 Agent**。

这个工具会移动你的文件，遵守约束比跑得快要重要。
