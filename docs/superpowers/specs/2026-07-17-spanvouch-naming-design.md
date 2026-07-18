# SpanVouch 品牌命名设计

> 状态：对话方案已批准，待书面复核
> 日期：2026-07-17
> 适用对象：开源系统、GitHub 仓库、工程交付与论文 artifact
> 方法名：IVAD（Independently Verified Agent Diagnosis）
> 历史代号：AFC（Agent Failure Clinic）
> 前置调研：`docs/research/2026-07-17-ivad-project-naming-research.md`

## 1. 决策

项目的公开品牌正式定为 **SpanVouch**。

```text
SpanVouch
Evidence-backed diagnosis for AI agent traces.
```

中文描述采用：

> 面向 AI Agent 轨迹的证据化故障诊断。

品牌、方法与历史代号分层如下：

| 名称 | 角色 | 使用位置 |
|---|---|---|
| **SpanVouch** | 开源系统与工程品牌 | GitHub、README、产品、CLI、Python distribution、演示、简历 |
| **IVAD** | 论文方法与验证协议 | 论文标题/正文、算法、实验、研究规格 |
| **AFC** | 历史内部代号 | 旧文档、Git 历史、旧 artifact provenance，不再用于新的公开标题 |

## 2. 名称语义

### 2.1 Span

`Span` 是 OpenTelemetry 和 TraceIR 中可定位、可重放、可验证的执行证据单元。它把品牌与项目真正处理的对象绑定起来，而不是使用宽泛的 `Agent`、`AI` 或 `Ops`。

### 2.2 Vouch

`Vouch` 表示系统只在诊断 claim 获得轨迹证据、独立验证和风险控制支持时为其背书。系统无法背书时应补证或弃答，因此该词同时表达验证能力与保守边界，不等同于承诺绝对正确。

### 2.3 组合含义

SpanVouch 的核心品牌承诺是：

> 不根据听起来合理的解释接受诊断，而是根据可重新解析的 trace/span 证据决定是否接受。

## 3. 推荐写法

### 3.1 首次出现

```text
SpanVouch is an open-source system for evidence-backed diagnosis of AI agent failures.
It implements IVAD, an independently verified and risk-controlled diagnosis protocol.
```

### 3.2 简短描述

```text
Evidence-backed diagnosis for AI agent traces.
```

### 3.3 简历写法

```text
SpanVouch — Open-source AI agent reliability and failure-diagnosis system
```

### 3.4 论文工作标题

```text
SpanVouch: Risk-Controlled, Independently Verified Failure Diagnosis for AI Agents
```

论文可以调整副标题，但系统名固定为 SpanVouch，方法名固定为 IVAD。

## 4. 命名规范

- 品牌展示：`SpanVouch`；
- 仓库候选名：`spanvouch`；
- Python distribution 候选名：`spanvouch`；
- Python import namespace 目标：`spanvouch`；
- 主 CLI 目标：`spanvouch`；
- 环境变量前缀目标：`SPANVOUCH_`；
- Docker image 和 Compose service 前缀目标：`spanvouch`；
- 方法缩写必须大写：`IVAD`；
- 不使用 `Span Vouch`、`Span-Vouch`、`SPANVOUCH` 作为普通正文品牌写法。

以上 namespace 是后续迁移目标，不代表本命名规格已经执行代码、仓库或包名修改。

## 5. 迁移边界

### 5.1 当前不执行

本次命名决策只修改规划文档。明确不在本次执行：

- 不重命名 GitHub 仓库或 remote；
- 不修改 `src/afc`；
- 不修改现有 CLI、API title、环境变量或 Docker service；
- 不批量改写历史设计、交接和实验报告；
- 不改变 Phase 3 范围或验收标准。

### 5.2 后续执行时机

Phase 3 按现有 AFC namespace 完成。Phase 4 Research Foundation 的第一个独立迁移任务负责公开 namespace 切换，因为 Phase 4 会冻结长期契约、artifact 和包结构。

首次公开稳定版本之前完成一次性迁移：

1. 更新公开 README、仓库描述和文档入口；
2. 将 Python distribution、import namespace 和 CLI 切换到 `spanvouch`；
3. 将新环境变量和容器名称切换到 `SPANVOUCH_` / `spanvouch`；
4. 保留旧 artifact 中的 AFC provenance，不伪造历史名称；
5. 在迁移说明中记录旧名与新名的映射；
6. 完成全量测试、安装测试、CLI smoke 和 secret scan 后再改 GitHub 仓库名。

由于项目尚未发布稳定公共 API，规划上不长期维护 `afc` import alias；如果公开发布前已经出现外部使用者，再由迁移规格重新评估兼容期。

## 6. AFC 与 IVAD 的保留规则

### 6.1 AFC

AFC 不再出现在新建公开文档的主标题、仓库名或产品介绍中。以下情况可以保留：

- 描述项目历史时写作 `SpanVouch (formerly Agent Failure Clinic)`；
- 旧 commit、旧数据 manifest 或旧实验 artifact 的 provenance；
- 为保持历史结果可验证而不能改写的 frozen bytes。

### 6.2 IVAD

IVAD 继续用于：

- Claim–Evidence Contract；
- dual-channel verification；
- controlled failure separation；
- Conformal selective risk control；
- layered evidence acquisition；
- 论文算法、消融和研究问题。

不把 IVAD 当作产品主品牌，也不把 SpanVouch 当作单一算法名称。

## 7. 基础可用性检查

2026-07-17 的初筛结果：

- PyPI exact project endpoint 未返回现有 `spanvouch` 项目；
- GitHub repository-name exact/substring 初筛未发现 `SpanVouch` 仓库；
- Verisign `.com` RDAP 未返回 `spanvouch.com` 注册记录。

该结果只用于排除明显碰撞，不是商标、公司名称、恶意包、社交账号或法律审查。正式公开前必须重新检查 GitHub、PyPI、主要域名、npm/容器 registry、搜索引擎和目标法域商标数据库，并由用户决定是否购买域名或注册商标。

## 8. 视觉与语气原则

- 视觉核心应围绕 span、证据连接、验证印章或有界路径；
- 避免医疗诊所、机器人头像和普通聊天气泡，摆脱 AFC 的“Clinic”旧意象；
- 避免盾牌加对勾的通用安全模板，防止看起来像网络安全扫描器；
- 品牌语气强调可验证、克制和可审计，不使用 `perfect`、`guaranteed truth` 或 `zero hallucination`；
- Logo、配色和网站视觉属于后续独立品牌设计，不在本规格内生成。

## 9. 验收标准

命名迁移未来完成时必须满足：

- 新用户只看 README 首屏即可区分 SpanVouch 系统与 IVAD 方法；
- GitHub、distribution、import、CLI 和文档入口使用一致拼写；
- frozen artifact 和历史实验仍可按原 AFC provenance 验证；
- 全仓库不存在误导性的双重主品牌；
- 公开描述不夸大风险保证或验证能力；
- 发布前可用性与法律风险检查有日期、来源和负责人记录。

## 10. 最终结论

```text
Public system / repository: SpanVouch
Research method:             IVAD
Historical codename:         AFC
Primary tagline:             Evidence-backed diagnosis for AI agent traces.
```

该结构让工程品牌保持简洁、可传播，同时让论文方法保持精确、可扩展。后续即使 IVAD 算法演进，SpanVouch 仍可作为长期系统品牌。
