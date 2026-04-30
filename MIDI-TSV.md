下面给你一个可以直接作为项目规范的 **MIDI-TSV v0.1 设计**。核心原则是：

```text
1. 文件扩展名直接使用 .tsv
2. 大 TSV = 多个 Slice TSV 的连接体
3. 每个 Slice 内部用 T block 对应 ABCX 的 [V:1] / [V:2]
4. 音符使用 note-centric 表示，不使用 note_on / note_off 低层事件
5. 时间只使用 t / dur，不提前假设 bar / beat / pos
6. Perform-LM 只处理短 Slice，不直接处理完整 TSV
```

这个设计和你 Performance Model 文档里的核心观点一致：Performance MIDI 的价值在于 timing deviation、duration、velocity、pedal、micro-timing 等演奏表达信息，而不是把它过早量化成乐谱网格。

---

# MIDI-TSV v0.1 设计

## 1. 文件命名

MIDI-TSV 文件直接加上 `.tsv` 后缀。

例如：

```text
nocturne_001.mid
→ nocturne_001.mid.tsv
```

## 2. 设计目标

MIDI-TSV 不是传统 MIDI CSV，也不是 REMI、BPE 或 MusicBERT token。它是一个 **面向 LLM 的 Performance MIDI 文本中间格式**。

它要满足四件事：

|目标|说明|
|---|---|
|MIDI 可转 TSV|用固定脚本把 MIDI 转成 TSV|
|TSV 可转 MIDI|用固定脚本把 TSV 重新渲染成 MIDI|
|TSV 可切片|一个完整 TSV 内部包含多个 Slice|
|Slice 可喂 LLM|Perform-LM 每次只处理一个短 Slice|

所以完整流程是：

```text
MIDI
→ full .tsv
→ slice .tsv
→ Perform-LM
→ ABCX fragment / MIDI-TSV fragment
→ merge
→ full ABCX / full MIDI
```

---

# 3. 文件整体结构

一个完整 `.tsv` 文件由三层组成：

```text
Global Header
Slice Block 1
Slice Block 2
Slice Block 3
...
```

其中：

```text
大 TSV = 多个小 Slice TSV 的顺序连接
```

示例：

```text
# midi-tsv v0.1
# source=example.mid
# unit=tick
# tpq=480
# pitch=abc-absolute
# voice_map=T1:V1,T2:V2

S	1	0	3840
T	1
C	0	482	54
E	41	469	47
G	67	451	42
P	20	88
P	1850	0

T	2
C,	0	920	42
G,	965	850	45

S	2	3840	7680
T	1
D	0	420	60
E	460	410	63

T	2
G,	0	900	44
C	920	880	46
```

---

# 4. 基本记录类型

每一行是一个 record。字段之间用 **tab** 分隔。

## 4.1 注释 / 元信息

以 `#` 开头：

```text
# midi-tsv v0.1
# unit=tick
# tpq=480
# pitch=abc-absolute
# voice_map=T1:V1,T2:V2
```

推荐元信息：

|字段|含义|
|---|---|
|`source`|原 MIDI 文件名|
|`unit`|时间单位，推荐 `tick`|
|`tpq`|ticks per quarter note|
|`pitch`|pitch 表示方式|
|`voice_map`|track 到 ABCX voice 的映射|

---

## 4.2 Slice 记录

```text
S	slice_id	start_tick	end_tick
```

例如：

```text
S	1	0	3840
S	2	3840	7680
```

含义：

|字段|含义|
|---|---|
|`S`|Slice block 开始|
|`slice_id`|切片编号|
|`start_tick`|该 slice 在全曲中的开始 tick|
|`end_tick`|该 slice 在全曲中的结束 tick|

注意：  
**Slice 内部所有音符的 `t` 都是相对时间，从 0 开始。**

也就是：

```text
absolute_tick = slice_start_tick + local_t
```

---

## 4.3 Track 切换记录

```text
T	track_id
```

例如：

```text
T	1
N	0	482	C	54
N	41	469	E	47

T	2
N	0	920	C,	42
```

含义：

```text
T 1 之后的事件属于 Track 1
T 2 之后的事件属于 Track 2
```

`T` 后面跟随的事件可以是 `pitch t dur vel`（音符）或 `P t val`（踏板）。

这和 ABCX 的 voice 很自然对应：

```text
T	1  → [V:1]
T	2  → [V:2]
T	3  → [V:3]
```

所以可以在 header 写：

```text
# voice_map=T1:V1,T2:V2,T3:V3
```

---

## 4.4 Note 记录

音符记录以 **ABC 音高**作为第一个字段，不使用 `N` 前缀：

```text
pitch	t	dur	vel
```

例如：

```text
C	0	482	54
E	41	469	47
G	67	451	42
D	965	418	58
```

含义：

|字段|含义|
|---|---|
|`pitch`|ABC-like pitch，作为行标识|
|`t`|onset time，slice 内相对 tick，从 0 开始|
|`dur`|duration，tick|
|`vel`|MIDI velocity，0–127|

这是一种 **note-centric 表示**。  
一个音符只占一行，不拆成 `note_on` 和 `note_off`。

解析时，如果行的第一个字段符合 ABC 音高格式（`[_^=]*[A-Ga-g]['|,]*`），则视为音符记录。

---

## 4.5 Pedal 记录

第一版只保留 sustain pedal 即可：

```text
P	t	val
```

例如：

```text
P	20	88
P	1850	0
```

含义：

|字段|含义|
|---|---|
|`P`|sustain pedal event|
|`t`|pedal event time|
|`val`|CC64 value，0–127|

其中：

```text
P 20 88   = sustain pedal down / half-pedal
P 1850 0  = sustain pedal up
```

如果后续要支持更多 CC，可以扩展为：

```text
C	t	cc	val
```

例如：

```text
C	20	64	88
C	1850	64	0
C	300	67	70
```

但第一版建议只用 `P`。

---

## 4.6 Tempo / Meter / Key 记录，可选

如果需要保留 tempo、拍号、调号变化，可以用：

```text
Q	t	bpm
M	t	meter
K	t	key
```

例如：

```text
Q	0	72
M	0	4/4
K	0	C
```

但注意：  
这些只是全局/局部提示，不代表已经进行了小节对齐。

对于真实 Performance MIDI，不要强行加入：

```text
bar
beat
pos
MBT
```

因为这些可能和真实乐谱没有关系。

---

# 5. Pitch 表示

## 5.1 使用 ABC-like pitch

你提出“使用 ABC 的 pitch 符号，而不是 MIDI pitch number”，这是合理的。

例如：

```text
C
D
E
F
G
A
B
c
d
e
C,
C,,
c'
^F
_B
=F
```

推荐使用 **ABC-like absolute pitch**：

```text
pitch=abc-absolute
```

意思是：

> TSV 中的 pitch 是绝对音高拼写，不依赖 key signature 的省略规则。

例如：

```text
^F
_B
=F
```

直接表示具体音高。

不要让 TSV pitch 依赖上下文，比如：

```text
K:G
F
```

在 ABC 里可能表示 F#，但在 TSV 中最好不要这样。TSV 里 pitch 应该尽量自包含。

---

## 5.2 MIDI pitch number 到 ABC pitch 的转换

原始 MIDI 里只有数值音高，例如：

```text
60
61
62
```

没有告诉你它应该写成：

```text
^C
```

还是：

```text
_D
```

所以 MIDI → TSV 时，需要一个 pitch spelling 前处理器。

第一版可以用简单规则：

```text
key=C 时：
60 → C
61 → ^C
62 → D
63 → ^D
64 → E
65 → F
66 → ^F
67 → G
68 → ^G
69 → A
70 → ^A
71 → B
72 → c
```

后续可以根据 key、上下文、和声再优化 enharmonic spelling。

---

# 6. MIDI → TSV 转换

## 6.1 输入

标准 MIDI 文件：

```text
example.mid
```

MIDI 内部通常是：

```text
Header
Track 1: delta-time event stream
Track 2: delta-time event stream
...
```

每个 track 内部是事件流，包括：

```text
note_on
note_off
control_change
tempo
time_signature
key_signature
marker
...
```

但 TSV 不直接保存低层事件流，而是转成 note-centric 表示。

---

## 6.2 转换步骤

### Step 1：读取 MIDI

读取：

```text
tpq
tracks
tempo events
time signature events
key signature events
note_on / note_off
control_change
```

---

### Step 2：delta time 转 absolute tick

MIDI track 内部的时间通常是 delta time。

需要转成绝对 tick：

```text
abs_tick_i = abs_tick_{i-1} + delta_tick_i
```

---

### Step 3：配对 note_on / note_off

把：

```text
note_on pitch=60 vel=54 at tick=0
note_off pitch=60 at tick=482
```

转成：

```text
C	0	482	54
```

即：

```text
pitch = midi_pitch_to_abc_pitch(60)
t = note_on_tick
dur = note_off_tick - note_on_tick
vel = note_on_velocity
```

注意：

```text
note_on velocity=0
```

在 MIDI 中通常也表示 note_off。

---

### Step 4：处理 pedal

把 sustain pedal CC64 转成：

```text
P	t	val
```

例如：

```text
control_change cc=64 value=88 tick=20
→ P	20	88
```

第一版只保留 CC64 即可。

---

### Step 5：生成初始完整事件表

此时每个 track 得到：

```text
T	1
N	...
P	...

T	2
N	...
P	...
```

---

### Step 6：切片

切片之后，大 TSV 写成多个 `S block`：

```text
S	1	start	end
T	1
...
T	2
...

S	2	start	end
T	1
...
T	2
...
```

每个 Slice 内部的 `t` 改成相对时间：

```text
local_t = absolute_tick - slice_start_tick
```

---

# 7. TSV → MIDI 转换

## 7.1 输入

```text
example.tsv
```

---

## 7.2 转换步骤

### Step 1：读取 header

读取：

```text
unit=tick
tpq=480
pitch=abc-absolute
```

---

### Step 2：逐个 Slice 解析

遇到：

```text
S	2	3840	7680
```

记录：

```text
current_slice_start = 3840
```

---

### Step 3：逐个 Track 解析

遇到：

```text
T	1
```

记录：

```text
current_track = 1
```

---

### Step 4：还原 note_on / note_off

对于：

```text
N	460	410	E	63
```

计算：

```text
pitch = "E"
abs_on = current_slice_start + 460
abs_off = abs_on + 410
pitch_number = abc_pitch_to_midi("E")
velocity = 63
```

生成：

```text
note_on  tick=abs_on  pitch=64 velocity=63
note_off tick=abs_off pitch=64 velocity=0
```

---

### Step 5：还原 pedal

对于：

```text
P	20	88
```

生成：

```text
control_change tick=current_slice_start+20 cc=64 value=88
```

---

### Step 6：按 track 聚合并排序

每个 track 内部按照绝对 tick 排序。

如果同一 tick 有多个事件，建议顺序为：

```text
tempo / meter / key
pedal
note_off
note_on
```

或者更保守：

```text
note_off before note_on
```

避免同 pitch 重叠时出错。

---

### Step 7：absolute tick 转 delta time

MIDI 写出时需要 delta time：

```text
delta_i = abs_tick_i - abs_tick_{i-1}
```

---

### Step 8：写出 MIDI

根据：

```text
tpq
tracks
events
```

写出：

```text
example.reconstructed.mid
```

注意：  
这个转换是 **musically reversible**，不保证 byte-level 完全一致。

也就是说，它可以保留：

```text
notes
durations
velocities
pedal events
track structure
tempo / meter / key metadata
```

但不一定保留：

```text
原始 running status
某些 text meta event
DAW 私有事件
原始事件排序的全部细节
```

---

# 8. MIDI 切成很多小段的方法

## 8.1 切片目标

切片不是按小节切，因为真实 MIDI 可能根本没有可靠小节线。

切片目标应该是：

```text
接近乐句
但有长度上限
并且保留多 track 同一时间窗口
```

也就是说：

```text
不要：每个 track 单独切
推荐：按全局时间切，每个 slice 内部保留所有 T block
```

---

## 8.2 切片基本原则

每个 Slice 应该满足：

|约束|建议|
|---|---|
|最短长度|3–5 秒等价 tick|
|目标长度|5–15 秒|
|最长长度|20 秒左右|
|note 数上限|80–160 个 note|
|track|保留该时间窗口内所有 track|
|时间|slice 内 `t` 从 0 开始|

如果使用 tick 而不是秒，可以通过 tempo 粗略换算。  
例如 `tpq=480, qpm=120` 时：

```text
1 beat = 480 tick
1 second = 960 tick
10 seconds ≈ 9600 tick
```

---

## 8.3 候选切点

优先选择乐句边界，但第一版可以用启发式。

### 优先级 1：MIDI 自带 marker

如果有：

```text
marker
cue point
track name section
text event
```

例如：

```text
Intro
Verse
Chorus
A
B
```

可以作为候选切点。

---

### 优先级 2：全局静音点

寻找所有 track 同时接近静音的位置：

```text
相邻 note onset 间隔很大
所有 note 已经结束
sustain pedal 已释放
```

例如：

```text
global_gap > 960 tick
```

或换算成：

```text
gap > 800ms / 1000ms / 1500ms
```

这通常接近乐句边界。

---

### 优先级 3：低密度点

如果没有明显静音，可以找 note density 低的位置：

```text
某个时间附近 note 数较少
velocity 收束
长音结束
踏板释放
```

这不一定是乐句边界，但比强行等长切好。

---

### 优先级 4：长度上限强制切

如果超过最长长度还没有好切点，就强制切：

```text
当前 slice 长度 > max_len
→ 在最近的低密度点切
→ 如果没有低密度点，就直接切
```

---

## 8.4 切片算法

伪流程：

```text
Input: full note events sorted by absolute tick

1. 设定 min_len, target_len, max_len
2. 从 start=0 开始
3. 在 [start+min_len, start+max_len] 中寻找候选切点
4. 优先选择最接近 target_len 的 phrase-like boundary
5. 如果没有候选点，选择 start+target_len 或 start+max_len
6. 得到 end
7. 生成 Slice(start, end)
8. start=end，继续
```

---

## 8.5 音符跨 Slice 怎么办？

采用最简单规则：

> 音符属于其 onset 所在的 Slice。

例如：

```text
Slice 1: 0–3840
N absolute_t=3600 dur=800
```

这个音符虽然延续到 4400，但它仍然放在 Slice 1：

```text
S	1	0	3840
T	1
C	3600	800	60
```

不要为了边界把音符强行切断。

踏板也一样：

```text
P absolute_t=3500 val=88
```

属于 Slice 1。

后续如果需要，可以在 LLM 输入中加 overlap context，但存储格式第一版不要复杂化。

---

# 9. 大 TSV 与小 TSV 的关系

完整大 TSV：

```text
piece.tsv
```

本质上是：

```text
slice_001.tsv
+ slice_002.tsv
+ slice_003.tsv
+ ...
```

所以：

```text
大 TSV = 多个 S block 的连接
小 TSV = 单个 S block
```

从大 TSV 抽出一个小 TSV 时，只需取对应 `S block`。

例如大 TSV：

```text
S	2	3840	7680
T	1
D	0	420	60
E	460	410	63

T	2
G,	0	900	44
C	920	880	46
```

抽成 LLM 输入：

```text
<PMIDI>
# unit=tick
# tpq=480
# pitch=abc-absolute
# voice_map=T1:V1,T2:V2

S	2	3840	7680
T	1
D	0	420	60
E	460	410	63

T	2
G,	0	900	44
C	920	880	46
</PMIDI>
```

---

# 10. 作为 LLM 输入

## 10.1 MIDI-TSV → ABCX

输入模板：

```text
你是 Perform-LM。请将下面的 MIDI-TSV slice 还原为 ABCX 片段。

要求：
1. 根据音符时间关系推断节奏和小节线。
2. 不要把微小 timing deviation 当成复杂节奏。
3. T1 对应 [V:1]，T2 对应 [V:2]。
4. 只输出 ABCX，不要解释。

<MIDI-TSV>
# unit=tick
# tpq=480
# pitch=abc-absolute
# voice_map=T1:V1,T2:V2

S	2	3840	7680
T	1
D	0	420	60
E	460	410	63

T	2
G,	0	900	44
C	920	880	46
</MIDI-TSV>
```

输出：

```abc
<ABCX>
[V:1] D E ...
[V:2] G, C ...
</ABCX>
```

---

## 10.2 ABCX → MIDI-TSV

输入模板：

```text
你是 Perform-LM。请将下面的 ABCX 片段演奏化为 MIDI-TSV slice。

演奏要求：
- 旋律稍微突出
- 和弦轻微错开
- 乐句末尾略微放慢
- 踏板自然

要求：
1. 使用 MIDI-TSV v0.1。
2. 使用 tick 时间，tpq=480。
3. 使用 ABC-like pitch。
4. 不要输出解释。

<ABCX>
M:4/4
L:1/8
K:C
[V:1] C D E F
[V:2] C,2 G,2
</ABCX>
```

输出：

```text
<MIDI-TSV>
# unit=tick
# tpq=480
# pitch=abc-absolute
# voice_map=T1:V1,T2:V2

S	1	0	3840
T	1
N	0	455	C	68
N	485	430	D	70
N	970	440	E	72
N	1465	460	F	69

T	2
N	0	900	C,	45
N	965	880	G,	43
P	0	80
P	1800	0
</MIDI-TSV>
```

---

## 10.3 MIDI-TSV → 演奏分析

输入：

```text
请分析下面 MIDI-TSV slice 的演奏特点，重点分析 timing、velocity、articulation 和 pedal。

<MIDI-TSV>
...
</MIDI-TSV>
```

输出：

```text
这段演奏中，T1 的前三个音存在轻微错开，形成类似琶音化的和弦进入……
```

---

# 11. SFT 数据格式

如果用官方 LoRA SFT，可以用 ChatML / JSONL。  
MIDI-TSV 本身就是普通文本，不需要创建新 tokenizer。

示例：

```json
{
  "messages": [
    {
      "role": "system",
      "content": "你是 Perform-LM，负责 MIDI-TSV 与 ABCX 之间的短片段转换。"
    },
    {
      "role": "user",
      "content": "请将下面的 MIDI-TSV slice 还原为 ABCX。只输出 ABCX。\n\n<MIDI-TSV>\n# unit=tick\n# tpq=480\n# pitch=abc-absolute\n# voice_map=T1:V1,T2:V2\n\nS\t2\t3840\t7680\nT\t1\nN\t0\t420\tD\t60\nN\t460\t410\tE\t63\n\nT\t2\nN\t0\t900\tG,\t44\nN\t920\t880\tC\t46\n</MIDI-TSV>"
    },
    {
      "role": "assistant",
      "content": "<ABCX>\n[V:1] D E ...\n[V:2] G, C ...\n</ABCX>"
    }
  ]
}
```

---

# 12. Verifier

LLM 输出后必须经过校验。

## 12.1 MIDI-TSV 校验

检查：

```text
N 行字段数是否正确
t / dur 是否为非负整数
vel 是否在 0–127
pitch 是否能解析
P value 是否在 0–127
Slice 内 t 是否大致在范围内
T block 是否存在
```

## 12.2 ABCX 校验

检查：

```text
是否能 parse
[V:n] 是否和 Tn 对应
小节线是否合理
时值是否能闭合
pitch 是否合法
是否能 render
```

如果不通过：

```text
Verifier 报错
→ 把错误报告 + 原始输出返回给 Perform-LM
→ 重新生成该 Slice
```

不需要单独 Repair Agent，Generator / Perform-LM 本身可以根据错误报告返修。

---

# 13. 最终推荐规范摘要

最终 MIDI-TSV v0.1 可以固定成：

```text
# midi-tsv v0.1
# source=<source.mid>
# unit=tick
# tick_scale=<scale>
# tpq=<ticks_per_quarter>
# pitch=abc-absolute
# voice_map=T1:V1,T2:V2,...

S	<slice_id>	<start_tick>	<end_tick>
T	<track_id>
<pitch>	<t>	<dur>	<vel>
P	<t>	<val>

S	<slice_id>	<start_tick>	<end_tick>
T	<track_id>
<pitch>	<t>	<dur>	<vel>
...
```

其中：
- `start_tick` 是该 slice 中**第一个事件**的绝对 tick，内部所有 `t` 从此处开始（即首个音符的 `t=0`）
- 音符记录不使用 `N` 前缀，以 ABC 音高作为行标识
- 相邻 slice 的 `start_tick` 之间可能有空隙，空隙中的音符归入前一个 slice（onset 所在 slice）

完整系统：

```text
MIDI
→ MIDI-TSV full file: piece.tsv
→ Slice blocks: S1, S2, S3...
→ Perform-LM processes one S block at a time
→ Output ABCX fragments or MIDI-TSV fragments
→ Composer / script merges fragments
→ Full ABCX or reconstructed MIDI
```

一句话概括：

> **MIDI-TSV 是 Performance MIDI 的 note-centric、slice-aware、LLM-friendly 文本中间格式；完整 `.tsv` 文件由多个 Slice block 组成，LLM 每次只处理一个 Slice block。**