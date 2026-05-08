# MIDI-TSV 规范

**MIDI-TSV** 是一个面向 LLM 的 Performance MIDI 文本中间格式。

## 核心原则

1. 文件扩展名：`.tsv`
2. 统一 3 列格式：`<type> <time> <value>`
3. 音符使用 note-centric 表示（不使用 note_on/note_off）
4. 使用固定真实时间轴：`PPQ=50`，`120 BPM`，`1 tick = 10ms`
5. 专注钢琴独奏（单 track，无多音色）
6. 智能切片，每个 slice 适合 LLM 处理

## 版本历史

- **v0.1**：初始版本，支持多 track，4 列格式
- **v0.2**：简化为单 track，统一 3 列格式，扩展 CC 支持
- **v0.2 当前实现**：Note 使用 `<pitch>:<dur>`；原 MIDI tempo map / PPQ / tick 会烘焙到固定 `10ms` tick 时间轴

---

# 文件格式 (v0.2)

## 1. 整体结构

```tsv
# midi-tsv v0.2
# source=example.mid
# unit=tick
# tick_scale=1
# tpq=50
# tick_ms=10
# pitch=abc-absolute
# detected_key=C
# slice_type=segment   # 或 "measure"，决定使用 S 或 M 前缀
# tempo=0,500000

S1	0	1140
P	0	127
C:482	0	54
E:469	41	47
G:451	67	42
C:920	0	42
P	172	0
M	0	"Intro"

S2	1140	2280
D:420	0	60
E:410	460	63
```

## 2. 统一 3 列格式

所有记录统一为：

```
<type>	<time>	<value>
```

| 记录类型 | 第 1 列 | 第 2 列 | 第 3 列 |
|---------|--------|--------|--------|
| Slice | `S<id>` | start_tick | end_tick |
| Note | `<pitch>:<dur>` | onset_time | velocity |
| Sustain Pedal | `P` | time | value (0-127) |
| Soft Pedal | `P1` | time | value (0-127) |
| Sostenuto | `P2` | time | value (0-127) |
| Expression | `P3` | time | value (0-127) |
| Marker | `M` | time | "text" |

---

# 记录类型详解

## Slice / Measure 记录

```tsv
S<id>	<start_tick>	<end_tick>    # Segment 模式
M<id>	<start_tick>	<end_tick>    # Measure 模式
```

**Segment 示例**：
```tsv
S1	0	1140
S2	1140	2280
```

**Measure 示例**：
```tsv
M1	0	1200
M2	1200	2400
```

**说明**：
- `S` 前缀用于 Segment 模式（基于静音间隙启发式切分）
- `M` 前缀用于 Measure 模式（基于 annotation 文件的音乐小节切分）
- `start_tick` 是该 slice 的绝对起始 tick（macro-tick）
- `end_tick` 是该 slice 的结束 tick
- Slice 内所有事件的 time 都是相对于 `start_tick` 的偏移

---

## Note 记录

```tsv
<pitch>:<dur>	<t>	<vel>
```

**示例**：
```tsv
C:100	0	60
E:95	50	65
G:200	100	50
^F:150	200	70
_B:80	300	55
```

**说明**：
- 第 1 列：pitch 和 duration 使用冒号分隔
- 第 2 列：onset time（相对于 slice start）
- 第 3 列：velocity (0-127)

**解析规则**：
- 匹配 ABC pitch 模式：`^[_^=]*[A-Ga-g]['|,]*`
- 冒号后的数字部分为 duration

**示例解析**：
```
C:100    → pitch="C",    dur=100
^F:150   → pitch="^F",   dur=150
c':200   → pitch="c'",   dur=200
G,,:80   → pitch="G,,",  dur=80
```

---

## Pedal 记录

### Sustain Pedal (CC64)
```tsv
P	<t>	<val>
```

**示例**：
```tsv
P	0	127     # pedal down
P	500	0      # pedal up
P	600	64     # half pedal
```

### Soft Pedal (CC67)
```tsv
P1	<t>	<val>
```

**示例**：
```tsv
P1	0	100    # soft pedal down
P1	500	0     # soft pedal up
```

### Sostenuto Pedal (CC66)
```tsv
P2	<t>	<val>
```

**示例**：
```tsv
P2	100	127   # sostenuto on
P2	600	0     # sostenuto off
```

### Expression (CC11)
```tsv
P3	<t>	<val>
```

**示例**：
```tsv
P3	0	100    # full expression
P3	200	60    # reduce expression
```

**命名规则**：
- `P` = Sustain Pedal (CC64) - 最常用
- `P1` = Soft Pedal (CC67)
- `P2` = Sostenuto (CC66)
- `P3` = Expression (CC11)

---

## Marker 记录

```tsv
M	<t>	"<text>"
```

**示例**：
```tsv
M	0	"Intro"
M	1140	"Verse"
M	2280	"Chorus"
```

保留 MIDI 文件中的 marker/cue point，帮助 LLM 理解乐句结构。

---

# 固定时间轴机制

## 为什么需要固定时间轴？

Performance MIDI 的 tempo map、PPQ 和 tick 分辨率各不相同。MIDI-TSV 会先把原始 MIDI 的 tick 按 tempo map 转成真实时间，再量化到统一时间轴：

- `tpq = 50`
- `tempo = 500000`（120 BPM）
- `tick_scale = 1`
- `1 tick = 10ms`

```
seconds = original_tick_to_seconds(original_tick, original_tempo_map, original_tpq)
tsv_tick = round(seconds * 1000 / 10)
```

TSV 中的 `time`、`duration`、slice 边界都使用这个新 tick，不再使用原始 MIDI tick。

---

# Pitch 表示

## ABC Pitch 语法

```
C, D, E, F, G, A, B     # 低八度
C  D  E  F  G  A  B     # 中八度 (middle C = C)
c  d  e  f  g  a  b     # 高八度
c' d' e' f' g' a' b'    # 更高八度
^C  _D  =E              # 升、降、还原
```

## 智能 Pitch Spelling

**问题**：MIDI 只有数字音高（60, 61, 62...），不知道应该写成 `^C` 还是 `_D`。

**解决方案**：基于 slice 音列自动识别调性

### Step 1: 收集音高类

```python
def collect_pitch_classes(slice_notes):
    pitch_classes = set()
    for note in slice_notes:
        pitch_classes.add(note.midi_pitch % 12)
    return pitch_classes
```

### Step 2: 匹配最佳调性

```python
MAJOR_SCALES = {
    "C":  [0, 2, 4, 5, 7, 9, 11],
    "G":  [0, 2, 4, 6, 7, 9, 11],  # F#
    "D":  [1, 2, 4, 6, 7, 9, 11],  # F#, C#
    "F":  [0, 2, 4, 5, 7, 9, 10],  # Bb
    "Bb": [0, 2, 3, 5, 7, 9, 10],  # Bb, Eb
    "Eb": [0, 2, 3, 5, 7, 8, 10],  # Bb, Eb, Ab
    "Ab": [0, 1, 3, 5, 7, 8, 10],  # Bb, Eb, Ab, Db
    "Db": [0, 1, 3, 5, 6, 8, 10],  # all flats
    # ... 更多调性
}

def find_best_key(pitch_classes):
    best_key = "C"
    best_score = 0
    
    for key, scale in MAJOR_SCALES.items():
        score = len(pitch_classes & set(scale))
        if score > best_score:
            best_score = score
            best_key = key
    
    return best_key
```

### Step 3: 根据调性拼写

```python
KEY_ACCIDENTALS = {
    "C":  {},
    "G":  {6: "^F"},
    "D":  {6: "^F", 1: "^C"},
    "F":  {10: "_B"},
    "Db": {10: "_B", 3: "_E", 8: "_A", 1: "_D", 6: "_G"},
    # ...
}

def midi_pitch_to_abc_smart(pitch, key):
    pitch_class = pitch % 12
    octave = pitch // 12 - 5
    
    if pitch_class in KEY_ACCIDENTALS[key]:
        spelled = KEY_ACCIDENTALS[key][pitch_class]
    else:
        natural_notes = {0:"C", 2:"D", 4:"E", 5:"F", 7:"G", 9:"A", 11:"B"}
        spelled = natural_notes.get(pitch_class, "C")
    
    # 添加八度标记
    if octave > 0:
        return f"{spelled.lower()}{'\'' * (octave - 1)}"
    elif octave < 0:
        return f"{spelled}{',' * (-octave)}"
    return spelled
```

### 效果示例

**输入 MIDI**：`60, 61, 63, 65, 66, 68, 70, 72`

**简单规则**（v0.1）：
```tsv
C100	0	60
^C100	100	60
^D100	200	60
F100	300	60
```

**智能识别**（v0.2，识别为 Db major）：
```tsv
# detected_key=Db
C100	0	60
_D100	100	60
_E100	200	60
F100	300	60
```

---

# 切片算法

## 切片目标

- 接近乐句边界
- 长度在合理范围内（10-20 秒）
- 保留完整的音乐片段

## 切片参数

| 参数 | 值 |
|-----|---|
| 最短长度 | 10 秒 |
| 目标长度 | 15 秒 |
| 最长长度 | 20 秒 |
| 最小间隙 | 350ms |

## 智能切点选择

### 寻找候选切点

```python
def _find_weak_cut_candidates(notes, pedals, tick_scale, min_gap_macro):
    # 1. 构建音符和踏板的活跃区间
    intervals = []
    for note in notes:
        start = to_macro(note["t"], tick_scale)
        end = to_macro(note["t"] + note["dur"], tick_scale)
        intervals.append((start, end))
    
    # 2. 踏板按下期间也视为活跃区间
    pedal_down_tick = None
    for pedal in sorted(pedals, key=lambda x: x["t"]):
        mt = to_macro(pedal["t"], tick_scale)
        if pedal["val"] >= 64 and pedal_down_tick is None:
            pedal_down_tick = mt
        elif pedal["val"] < 64 and pedal_down_tick is not None:
            intervals.append((pedal_down_tick, mt))
            pedal_down_tick = None
    
    # 3. 合并重叠区间
    intervals.sort()
    merged = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    
    # 4. 在间隙中间位置作为候选切点
    cuts = []
    for i in range(1, len(merged)):
        gap = merged[i][0] - merged[i-1][1]
        if gap >= min_gap_macro:
            cuts.append(round((merged[i-1][1] + merged[i][0]) / 2))
    
    return sorted(set(cuts))
```

### 贪心切片

```python
def create_slices(notes, pedals, end_tick, ...):
    cut_candidates = _find_weak_cut_candidates(...)
    
    slices = []
    start_macro = 0
    
    while end_macro - start_macro > max_macro:
        min_cut = start_macro + min_macro
        max_cut = min(start_macro + max_macro, end_macro)
        target_cut = min(start_macro + target_macro, max_cut)
        
        # 选择最接近 target_cut 的候选点
        candidates = [c for c in cut_candidates if min_cut < c < max_cut]
        cut = min(candidates, key=lambda c: abs(c - target_cut)) if candidates else target_cut
        
        slices.append({"id": len(slices)+1, "start": start_macro*tick_scale, "end": cut*tick_scale})
        start_macro = cut
    
    slices.append({"id": len(slices)+1, "start": start_macro*tick_scale, "end": end_tick})
    return slices
```

## 优势

- ✅ 避免在音符或踏板活跃期间切断
- ✅ 优先选择自然的静音点
- ✅ 保证每个 slice 长度合理
- ✅ 对 LLM 友好

---

## Measure 模式（小节切分）

除了基于静音间隙的 Segment 模式（`S` 前缀），MIDI-TSV 还支持基于音乐小节结构的 Measure 模式（`M` 前缀）。

### 模式标识

通过 header 中的 `# slice_type=` 区分：

```
# slice_type=segment   # 基于静音间隙的启发式切片
# slice_type=measure   # 基于注释文件的音乐小节切片
```

### Measure 记录格式

```tsv
M<id>	<start_tick>	<end_tick>
```

**示例**：
```tsv
M1	0	1200
M2	1200	2400
M3	2400	3600
```

与 `S` 前缀的 Slice 记录类似，`M` 前缀表示该切片对应一个音乐小节。start/end tick 为绝对 tick 值，slice 内事件的 time 相对于 `start_tick`。

### Annotation 文件格式

Measure 模式需要一个 annotation 文件（通常为 `*_annotations.txt`）来标记 downbeat（小节起始）和 beat 位置：

```
time	offset	type[,time_signature[,offset_beat]]
```

| 字段 | 说明 |
|------|------|
| `time` | 时间（秒） |
| `offset` | 偏移（秒，通常与 time 相同） |
| `type` | `db` = downbeat（小节起始），`b` = beat |
| `time_signature` | 可选，如 `4/4`、`3/4`，仅在 downbeat 上出现 |
| `offset_beat` | 可选，从 0 开始的小节内拍数 |

**示例**：
```
0.0	0.0	db,4/4,0
0.491804	0.491804	b
0.983608	0.983608	b
1.475412	1.475412	b
1.967216	1.967216	db,4/4,0
```

每个 `db` 事件标记一个小节的开始，两个连续 `db` 之间的时间范围构成一个小节。

### 小节构建

1. 从 annotation 文件中提取所有 `db` 事件作为小节边界
2. 将 MIDI 音符/踏板事件从原始 tick 转换为 annotation 时间轴的 tick
3. 按 downbeat 边界划分小节
4. 第一个小节从时间 0 开始，包含该时间点之前的所有事件

### M1 之前的踏板处理

第一个小节（M1）之前可能已有踏板事件（乐曲开始前的踏板按下）。这些踏板事件**不会被丢弃**，而是保留在 M1 内，offset 写为 `0`：

```tsv
# midi-tsv v0.2
# slice_type=measure
# ...

M1	0	1200
P	0	127          # 乐曲开始前的踏板，offset=0
P1	0	64
^E:400	0	80       # M1 内的第一个音符
```

注意：M1 的 `start_tick` 仍然是其真实的起始 tick（可能为 0 或更早），pre-measure 踏板以 offset 0 写入以保持格式一致性。

### 智能踏板量化

小节内两个连续事件（音符或边界）之间可能有大量踏板事件（上百个）。采用分段量化策略：

**步骤**：

1. 确定小节内的事件边界：`[measure_start, note_times..., measure_end]`
2. 在每个相邻事件对之间的区间内，分别进行量化
3. 每个区间内每种踏板类型最多保留 **5 个点**：
   - 第一个点
   - 最后一个点
   - 中间最多 3 个代表性极值点（峰值/谷值）

```python
# 小节内的事件边界
boundaries = [measure_start] + sorted(note_times) + [measure_end]

for i in range(len(boundaries) - 1):
    seg_start = boundaries[i]
    seg_end = boundaries[i + 1]
    # 对此区间内的踏板事件进行量化
    segment_pedals = [p for p in pedals if seg_start <= p["t"] < seg_end]
    quantized = smart_quantize(segment_pedals, max_points=5)
```

**量化细节**：

- **去冗余**：先过滤变化 ≤ 3 的事件（`abs(prev_value - curr_value) ≤ 3`）
- **极值选择**：在剩余事件中找到局部极大值和极小值，优先保留变化最大的 3 个点
- 这样可以在大幅减少事件数的同时，保留踏板曲线的整体形状

**效果**：原本一个小节内数百个踏板事件可减少到几十个，但踏板曲线的主要起伏特征被保留。

---

# 完整示例

## v0.1 格式（对比）

```tsv
# midi-tsv v0.1
# source=example.mid
# unit=tick
# tick_scale=10
# tpq=384
# voice_map=T1:V1,T2:V2

S	1	0	1140
T	1
C	0	482	54
E	41	469	47
P	20	88

T	2
C,	0	920	42
G,	965	850	45
```

## v0.2 格式（新设计）

```tsv
# midi-tsv v0.2
# source=example.mid
# unit=tick
# tick_scale=20
# tpq=384
# pitch=abc-absolute
# detected_key=C

S1	0	1140
C482	0	54
E469	41	47
P	20	88
C920	0	42
G850	965	45
M	0	"Intro"
```

## 对比

| 特性 | v0.1 | v0.2 |
|-----|------|------|
| 列数 | 4 列（不统一） | 3 列（统一） |
| Track | 多 track | 单 track |
| Note 格式 | `C  0  482  54` | `C482  0  54` |
| Slice 格式 | `S  1  0  1140` | `S1  0  1140` |
| CC 支持 | 仅 CC64 | CC64/67/66/11 |
| Marker | 不支持 | 支持 |
| Pitch Spelling | 简单规则 | 智能识别 |
| Token 数 | ~100 | ~70 |

---

# 转换算法

## MIDI → TSV v0.2

```python
def midi_to_tsv_v2(data: bytes, source: str) -> str:
    tpq, raw_tracks = parse_midi(data)
    
    # 1. 合并所有 track
    all_notes = []
    all_pedals = []
    all_markers = []
    
    for track_idx, events in enumerate(raw_tracks):
        tick = 0
        open_notes = defaultdict(list)
        
        for evt in events:
            tick += evt["delta"]
            
            if evt["type"] == "note_on":
                open_notes[evt["note"]].append({
                    "t": tick, "pitch": evt["note"], "vel": evt["velocity"]
                })
            elif evt["type"] == "note_off":
                if open_notes[evt["note"]]:
                    note = open_notes[evt["note"]].pop(0)
                    note["dur"] = tick - note["t"]
                    all_notes.append(note)
            elif evt["type"] == "control_change":
                cc = evt["controller"]
                cc_map = {64: "P", 67: "P1", 66: "P2", 11: "P3"}
                if cc in cc_map:
                    all_pedals.append({
                        "type": cc_map[cc], "t": tick, "val": evt["value"]
                    })
            elif evt["type"] == "meta" and evt.get("meta_type") == 0x06:
                all_markers.append({"t": tick, "text": evt.get("text", "")})
    
    # 2. 排序
    all_notes.sort(key=lambda n: n["t"])
    all_pedals.sort(key=lambda p: p["t"])
    
    # 3. 选择 tick_scale
    tick_scale = select_tick_scale(tpq, tempos)
    
    # 4. 切片
    slices = create_slices(all_notes, all_pedals, end_tick, tpq, tempos, tick_scale)
    
    # 5. 为每个 slice 识别调性
    for sl in slices:
        slice_notes = [n for n in all_notes if sl["start"] <= n["t"] < sl["end"]]
        sl["key"] = detect_key_from_notes(slice_notes)
    
    # 6. 生成 TSV
    lines = [
        "# midi-tsv v0.2",
        f"# source={source}",
        f"# unit=tick",
        f"# tick_scale={tick_scale}",
        f"# tpq={tpq}",
        f"# pitch=abc-absolute",
    ]
    
    for sl in slices:
        local_start = find_slice_local_start(all_notes, all_pedals, all_markers, sl)
        lines.append(f"S{sl['id']}\t{scale_tick(local_start, tick_scale)}\t{scale_tick(sl['end'], tick_scale)}")
        
        # 收集该 slice 的所有事件
        events = []
        
        for n in all_notes:
            if local_start <= n["t"] < sl["end"]:
                pitch_abc = midi_pitch_to_abc_smart(n["pitch"], sl["key"])
                dur_scaled = scale_duration(n["dur"], tick_scale)
                t_scaled = scale_tick(n["t"] - local_start, tick_scale)
                events.append({
                    "t": n["t"],
                    "line": f"{pitch_abc}{dur_scaled}\t{t_scaled}\t{n['vel']}"
                })
        
        for p in all_pedals:
            if local_start <= p["t"] < sl["end"]:
                t_scaled = scale_tick(p["t"] - local_start, tick_scale)
                events.append({
                    "t": p["t"],
                    "line": f"{p['type']}\t{t_scaled}\t{p['val']}"
                })
        
        for m in all_markers:
            if local_start <= m["t"] < sl["end"]:
                t_scaled = scale_tick(m["t"] - local_start, tick_scale)
                events.append({
                    "t": m["t"],
                    "line": f"M\t{t_scaled}\t\"{m['text']}\""
                })
        
        events.sort(key=lambda e: e["t"])
        for e in events:
            lines.append(e["line"])
        lines.append("")
    
    return "\n".join(lines)
```

## TSV v0.2 → MIDI

```python
def tsv_v2_to_midi(tsv: str) -> bytes:
    meta = _parse_tsv_meta(tsv)
    events = []
    current_slice_start = 0
    
    for line_idx, raw_line in enumerate(tsv.splitlines()):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        
        fields = raw_line.split("\t")
        record_type = fields[0]
        
        if record_type.startswith("S"):
            # Slice 记录: S1, S2, ...
            current_slice_start = int(fields[1]) * meta["tick_scale"]
        
        elif _is_note_record(record_type):
            # 解析 pitch 和 duration
            pitch_abc, dur = _parse_note_record(record_type)
            dur = dur * meta["tick_scale"]
            t = int(fields[1]) * meta["tick_scale"]
            vel = int(fields[2])
            
            pitch_midi = abc_pitch_to_midi(pitch_abc)
            abs_t = current_slice_start + t
            
            events.append({
                "tick": abs_t, "order": 3,
                "type": "note_on", "channel": 0, "note": pitch_midi, "velocity": vel
            })
            events.append({
                "tick": abs_t + dur, "order": 2,
                "type": "note_off", "channel": 0, "note": pitch_midi, "velocity": 0
            })
        
        elif record_type in ["P", "P1", "P2", "P3"]:
            # Pedal 记录
            t = int(fields[1]) * meta["tick_scale"]
            val = int(fields[2])
            abs_t = current_slice_start + t
            
            cc_map = {"P": 64, "P1": 67, "P2": 66, "P3": 11}
            events.append({
                "tick": abs_t, "order": 1,
                "type": "control_change", "channel": 0,
                "controller": cc_map[record_type], "value": val
            })
    
    events.sort(key=lambda e: (e["tick"], e["order"]))
    delta_events = _to_delta_events(events)
    return write_midi(meta["tpq"], [delta_events])

def _is_note_record(s: str) -> bool:
    # 匹配 ABC pitch 开头
    return bool(re.match(r'^[_^=]*[A-Ga-g][\'|,]*\d+$', s))

def _parse_note_record(s: str) -> tuple[str, int]:
    # 分离 pitch 和 duration
    match = re.match(r'^([_^=]*[A-Ga-g][\'|,]*)(\d+)$', s)
    if match:
        return match.group(1), int(match.group(2))
    raise ValueError(f"Invalid note record: {s}")
```

---

# 作为 LLM 输入

## MIDI-TSV → ABCX

```text
你是 Perform-LM。请将下面的 MIDI-TSV slice 还原为 ABCX 片段。

要求：
1. 根据音符时间关系推断节奏和小节线
2. 不要把微小 timing deviation 当成复杂节奏
3. 只输出 ABCX，不要解释

<MIDI-TSV>
# unit=tick
# tick_scale=20
# tpq=384
# detected_key=C

S1	0	1140
D420	0	60
E410	460	63
G900	0	44
C880	920	46
</MIDI-TSV>
```

## ABCX → MIDI-TSV

```text
你是 Perform-LM。请将下面的 ABCX 片段演奏化为 MIDI-TSV。

演奏要求：
- 旋律稍微突出
- 和弦轻微错开
- 乐句末尾略微放慢
- 踏板自然

要求：
1. 使用 MIDI-TSV v0.2
2. 使用 tick_scale=20
3. 不要输出解释

<ABCX>
M:4/4
L:1/8
K:C
C D E F | G2 A2 |
</ABCX>
```

输出：

```tsv
<MIDI-TSV>
# midi-tsv v0.2
# unit=tick
# tick_scale=20
# tpq=384

S1	0	200
C23	0	68
D22	24	70
E23	49	72
F24	75	69
G46	100	75
A45	150	73
P	0	80
P	180	0
</MIDI-TSV>
```

---

# 优势总结

## 相比 v0.1

| 特性 | v0.1 | v0.2 | 改进 |
|-----|------|------|------|
| 列数 | 4 列 | 3 列 | ✅ 更简洁 |
| Track | 多 track | 单 track | ✅ 专注钢琴 |
| Note 格式 | `C  0  482  54` | `C482  0  54` | ✅ 更紧凑 |
| CC 支持 | CC64 | CC64/67/66/11 | ✅ 表现力↑ |
| Pitch Spelling | 简单规则 | 智能识别 | ✅ 音乐理论↑ |
| Marker | ❌ | ✅ | ✅ 结构清晰 |
| Token 效率 | 100% | 70% | ✅ 节省 30% |

## 对 LLM 的优势

1. ✅ **统一格式**：3 列，降低解析复杂度
2. ✅ **语义清晰**：`C482` 直观表示"C 音持续 482 ticks"
3. ✅ **无需声部推理**：所有音符在同一时间线
4. ✅ **调性提示**：`detected_key` 帮助理解和声
5. ✅ **结构标记**：Marker 提供乐句边界
6. ✅ **粗量化**：tick_scale=20，数值更小

---

# 实现计划

## Phase 1: 核心功能（1 周）
- [ ] 实现 3 列统一格式
- [ ] 移除 Track 支持
- [ ] Note 格式改为 `pitch+dur`（无分隔符）
- [ ] Slice 格式改为 `S:id`
- [ ] 调整 tick_scale 默认值为 20

## Phase 2: 扩展 CC（3 天）
- [ ] 支持 CC67 (soft pedal) → `U`
- [ ] 支持 CC66 (sostenuto) → `O`
- [ ] 支持 CC11 (expression) → `X`

## Phase 3: 智能 Pitch Spelling（1 周）
- [ ] 实现调性识别算法
- [ ] 基于调性的音高拼写
- [ ] 测试不同调性

## Phase 4: Marker 支持（2 天）
- [ ] 解析 MIDI marker
- [ ] 输出到 TSV
- [ ] TSV → MIDI 还原

## Phase 5: 测试（3 天）
- [ ] 大量 MIDI 文件测试
- [ ] 与 v0.1 对比
- [ ] 性能优化

**总计**：约 2-3 周
