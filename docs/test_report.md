# 测试报告

本文档按 `demo_ui` 界面功能、抓包图、测试脚本输出和 demo 脚本输出记录。默认运行口径为 LLM + 实时数据。

## 1. demo_ui 界面功能

`demo_ui` 用于集中演示多进程 A2A 系统、MCP 调用和网络报文流转。

![demo_ui 主界面](UI.png)

| 功能区域 | 说明 |
|---|---|
| 节点启停 | 一键启动或停止 Registry、MCP Gateway、MCP Server、Agent、Coordinator。 |
| 拓扑展示 | 展示 User、Coordinator、Registry、Agent Pool、MCP Gateway、MCP Server 的通信关系。 |
| 任务提交 | 输入自然语言旅行需求，提交给 Coordinator，并查看最终旅行方案。 |
| 运行设置 | 切换 LLM、实时数据、本地数据备选等运行参数。 |
| 网络事件 | 展示任务提交、服务发现、Agent 派发、MCP 调用、结果回调等事件。 |
| 报文详情 | 查看 HTTP、TCP A2A、MCP JSON-RPC 报文内容。 |
| Agent 结果 | 查看 Weather、Attraction、Hotel、Traffic、Packing 等 Agent 的状态和摘要。 |
| Gateway 演示 | 展示 MCP Gateway 的统一入口调用、缓存指标和治理效果。 |

## 2. Wireshark 抓包图

Wireshark 抓包用于展示本地端口上的 HTTP、TCP A2A 与 MCP JSON-RPC 报文。TCP A2A 使用 4 字节长度前缀 + UTF-8 JSON body，HTTP 链路使用普通 JSON/JSON-RPC body。

![Wireshark 抓包示例](wireshark.png)

## 3. 测试配置

| 项目 | 内容 |
|---|---|
| 测试日期 | 2026-06-23 |
| 工作目录 | `E:\github\Agent-A2A` |
| Python | `.\.venv\Scripts\python.exe` |
| LLM 模式 | `A2A_USE_LLM=1` |
| 实时数据 | `A2A_REALTIME_MCP_ENABLED=true` |
| 模型服务 | `.env` 中已配置 ModelScope 相关参数 |
| 地图服务 | `.env` 中已配置高德 Web Key |

为便于报告阅读，以下命令关闭控制台内部网络日志；网络事件仍写入 `logs/demo_log.jsonl`，可在 `demo_ui` 中查看。

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
$env:A2A_USE_LLM='1'
$env:A2A_REALTIME_MCP_ENABLED='true'
$env:A2A_CONSOLE_NETWORK_LOG='0'
$env:A2A_LOG_LEVEL='OFF'
```

## 4. 测试脚本输出

### 4.1 `scripts/test_realtime_mcp.py`

命令：

```powershell
.\.venv\Scripts\python.exe scripts\test_realtime_mcp.py
```

完整输出：

```text
Realtime MCP smoke test
AMAP_WEB_KEY configured: True
- weather: provider=amap realtime=True
  forecast_days=3
- attraction: provider=amap+local_profile realtime=True
  spots=20
- hotel: provider=amap+local_profile realtime=True
  hotels=20
- traffic: provider=amap realtime=True
  segment 1: provider=amap realtime=True
  segment 2: provider=amap realtime=True
OK: weather, attraction, hotel and traffic route layer returned realtime-capable data.
Note: cache is intentionally not tested here; caching will be handled by MCP Gateway later.
```

### 4.2 `scripts/test_tcp_framing.py`

命令：

```powershell
.\.venv\Scripts\python.exe scripts\test_tcp_framing.py
```

完整输出：

```text
TCP framing tests passed: back-to-back frames, fragmented frame, send/receive round trip.
```

### 4.3 `python -m unittest discover -s tests -v`

命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

完整输出：

```text
test_agent_execution_failure_returns_standard_error_result (test_a2a_tcp_protocol.A2ARateLimitTests.test_agent_execution_failure_returns_standard_error_result) ... ok
test_agent_llm_rate_limit_becomes_successful_fallback_result (test_a2a_tcp_protocol.A2ARateLimitTests.test_agent_llm_rate_limit_becomes_successful_fallback_result) ... ok
test_coordinator_records_tcp_rate_limit_error_without_crashing (test_a2a_tcp_protocol.A2ARateLimitTests.test_coordinator_records_tcp_rate_limit_error_without_crashing) ... ok
test_unexpected_result_source_returns_error_frame (test_a2a_tcp_protocol.CoordinatorTcpCallbackTests.test_unexpected_result_source_returns_error_frame) ... ok
test_valid_task_result_frame_updates_task_and_returns_result_ack (test_a2a_tcp_protocol.CoordinatorTcpCallbackTests.test_valid_task_result_frame_updates_task_and_returns_result_ack) ... ok
test_stage_wait_does_not_extend_beyond_workflow_deadline (test_a2a_tcp_protocol.CoordinatorTimeoutTests.test_stage_wait_does_not_extend_beyond_workflow_deadline) ... ok
test_wait_for_target_marks_callback_timeout_as_dispatch_error (test_a2a_tcp_protocol.CoordinatorTimeoutTests.test_wait_for_target_marks_callback_timeout_as_dispatch_error) ... ok
test_wait_for_task_marks_remaining_targets_on_timeout (test_a2a_tcp_protocol.CoordinatorTimeoutTests.test_wait_for_task_marks_remaining_targets_on_timeout) ... ok
test_back_to_back_frames_do_not_merge_or_truncate (test_a2a_tcp_protocol.TcpFramingTests.test_back_to_back_frames_do_not_merge_or_truncate) ... ok
test_fragmented_frame_is_reassembled_by_recv_exact (test_a2a_tcp_protocol.TcpFramingTests.test_fragmented_frame_is_reassembled_by_recv_exact) ... ok
test_validate_envelope_rejects_wrong_type_and_missing_payload (test_a2a_tcp_protocol.TcpFramingTests.test_validate_envelope_rejects_wrong_type_and_missing_payload) ... ok
test_agent_city_extraction_prefers_destination_not_origin (test_travel_task_parsing.TravelTaskParsingTests.test_agent_city_extraction_prefers_destination_not_origin) ... ok
test_agent_parser_replaces_unknown_context_city_from_instruction (test_travel_task_parsing.TravelTaskParsingTests.test_agent_parser_replaces_unknown_context_city_from_instruction) ... ok
test_agent_parser_resolves_relative_day_start_date (test_travel_task_parsing.TravelTaskParsingTests.test_agent_parser_resolves_relative_day_start_date) ... ok
test_llm_normalization_uses_explicit_destination_from_question (test_travel_task_parsing.TravelTaskParsingTests.test_llm_normalization_uses_explicit_destination_from_question) ... ok
test_llm_split_context_keeps_rule_parsed_travel_fields (test_travel_task_parsing.TravelTaskParsingTests.test_llm_split_context_keeps_rule_parsed_travel_fields) ... ok
test_mock_attractions_do_not_relabel_unknown_city_as_beijing (test_travel_task_parsing.TravelTaskParsingTests.test_mock_attractions_do_not_relabel_unknown_city_as_beijing) ... ok
test_no_llm_coordinator_context_propagates_rule_parsed_date (test_travel_task_parsing.TravelTaskParsingTests.test_no_llm_coordinator_context_propagates_rule_parsed_date) ... ok
test_packing_temperature_range_does_not_parse_range_dash_as_negative (test_travel_task_parsing.TravelTaskParsingTests.test_packing_temperature_range_does_not_parse_range_dash_as_negative) ... ok
test_rule_fallback_preserves_non_default_destination (test_travel_task_parsing.TravelTaskParsingTests.test_rule_fallback_preserves_non_default_destination) ... ok

----------------------------------------------------------------------
Ran 20 tests in 1.720s

OK
```

## 5. Demo 脚本输出

### 5.1 `scripts/demo_gateway_cache.py`

命令：

```powershell
.\.venv\Scripts\python.exe scripts\demo_gateway_cache.py
```

完整输出：

```text
Runtime: mode=llm data=realtime
====== MCP Gateway Cache Demo ======
Gateway URL: http://127.0.0.1:8100/
Sending two identical get_weather requests with different JSON-RPC ids...

First response summary:
{
  "city": "北京市",
  "condition": "雷阵雨",
  "temperature": "19C-28C",
  "provider": "amap",
  "realtime": true,
  "id": "cache-demo-1"
}

Second response summary:
{
  "city": "北京市",
  "condition": "雷阵雨",
  "temperature": "19C-28C",
  "provider": "amap",
  "realtime": true,
  "id": "cache-demo-2"
}

Gateway metrics:
- total_requests: 2
- upstream_calls: 1
- cache_hits: 1
- cache_misses: 1
- error_count: 0

Method stats for get_weather:
{
  "requests": 2,
  "upstream_calls": 1,
  "cache_hits": 1,
  "cache_misses": 1,
  "coalesced_requests": 0,
  "rate_limited": 0,
  "circuit_open": 0,
  "error_count": 0,
  "avg_latency_ms": 365.43,
  "cache_hit_rate": 0.5
}

Expected cache effect:
- total_requests should be 2
- upstream_calls should be 1
- cache_hits should be 1
```

### 5.2 `scripts/demo_normal.py`

命令：

```powershell
.\.venv\Scripts\python.exe scripts\demo_normal.py --timeout 240
```

完整输出：

```text
Runtime: mode=llm data=realtime

====== Get Final Task (Time elapsed: 20890.96ms) ======

Task Status: completed

Final Answer:

下面是从上海到杭州的3天普通预算旅行方案，整体按公共交通优先安排。

一、天气与出行约束
- 杭州day1(2026-06-23)：小雨，气温20C-26C，建议减少长时间户外安排。
- 杭州day2(2026-06-24)：小雨，气温20C-25C，建议减少长时间户外安排。
- 杭州day3(2026-06-25)：小雨，气温22C-27C，建议减少长时间户外安排。
- 行李准备建议：
  * 证件：身份证、学生证/优惠证件（出行必备，前往西湖和灵隐寺可能需要购票或验证身份）
  * 洗漱用品：牙刷、毛巾、护肤品（日常所需，保持卫生）
  * 电子产品：手机、充电器、充电宝（保持联系与记录行程，杭州景点较多需频繁使用导航）
  * 衣物：短袖 T 恤、薄长裤、透气长袖衬衫、轻薄防晒外套、舒适运动鞋（气温在 20-27°C 之间，体感温热但雨天湿冷。建议采用洋葱式穿衣法：内层短袖排汗，中层长袖防雨防风，外层薄外套应对早晚温差及淋雨后降温。因全程步行游览西湖和灵隐寺，务必选择透气的运动鞋。）
  * 雨具：折叠雨伞、一次性雨衣、防水鞋套（三天均为小雨天气，且杭州多水景（西湖），需做好全身防雨准备，防止鞋子湿透影响步行体验。）

二、每日景点安排
- day1: 雨天室内安排。景点：灵隐寺、雷峰塔景区、西湖文化广场。区域：杭州西湖区。预计门票：灵隐寺:75；雷峰塔景区:40元。需提前预约：灵隐寺。
- day2: 雨天室内安排。景点：杭州西湖风景名胜区。区域：杭州西湖区。
- day3: 雨天室内安排。景点：西湖天地、河坊街景区。区域：杭州上城区。
景点门票小计：约40元。

三、住宿建议
- 建议住宿区域：西湖区。
- 推荐酒店：全季酒店(杭州黄龙店)，类型：住宿服务;宾馆酒店;经济型连锁酒店，最近地铁：龙翔桥站/凤起路站，参考价格：100-300元/晚。
住宿费用小计：约200-600元。

四、城市间交通方案
- 推荐：上海 -> 杭州，高铁二等座，约0小时36分钟，单程约100-150元。
- 往返估算：约200-300元。
- 备选：普速火车更省钱但耗时更长；飞机可能更快但价格波动较大且机场通勤成本更高。
城市间交通小计：约200-300元。

五、市内交通方案
- day1:
  - 全季酒店(杭州黄龙店) -> 灵隐寺: 公交，103路 -> 步行约1567m，约42分钟，费用约3.0元。
  - 灵隐寺 -> 雷峰塔景区: 公交，7路 -> 步行约1006m -> 12路区间 -> 步行约2074m，约106分钟，费用约4.0元。
  - 雷峰塔景区 -> 西湖文化广场: 地铁，地铁5号线 -> 步行约3094m -> 地铁1号线 -> 步行约480m，约74分钟，费用约3.0元。
  - 西湖文化广场 -> 全季酒店(杭州黄龙店): 地铁，地铁3号线 -> 步行约1702m，约36分钟，费用约3.0元。
- day2:
  - 全季酒店(杭州黄龙店) -> 杭州西湖风景名胜区: 公交，87路 -> 步行约955m，约44分钟，费用约2.0元。
  - 杭州西湖风景名胜区 -> 全季酒店(杭州黄龙店): 公交，194路 -> 步行约2656m，约58分钟，费用约3.0元。
- day3:
  - 全季酒店(杭州黄龙店) -> 西湖天地: 地铁，地铁3号线 -> 步行约1223m -> 地铁1号线 -> 步行约1071m，约53分钟，费用约3.0元。
  - 西湖天地 -> 河坊街景区: 公交，195路 -> 步行约714m，约27分钟，费用约2.0元。
- 预计市内交通费用：约23元。
市内交通小计：约23元。

六、费用总计
- 景点门票：约40元
- 住宿费用：约200-600元
- 城市间交通：约200-300元
- 市内交通：约23元
- 合计：约463-963元
- 说明：以上为示例估算，实际票价、酒店价格和交通费用以出行当天平台信息为准；餐饮和购物未计入。

七、提醒
- 需要预约的热门景点请提前通过官方渠道预约，并以当天开放信息为准。
- 交通耗时、费用和住宿价格为估算结果，实际出行前以当天平台信息为准。

Answers of Agents:
- weather_agent: success
已生成天气活动适配：杭州市2026-06-23小雨，气温20C-26C，适合户外[]，优先室内['day1', 'day2', 'day3']。
- attraction_agent: success
已根据普通预算，为杭州3天行程生成结构化景点规划
- packing_agent: success
杭州三日小雨旅行，重点携带雨具与透气速干衣物，采用分层穿衣法应对 20-27°C 的湿润气候，确保步行游览舒适。
- hotel_agent: success
已先根据景点分布选择住宿区域：西湖区，再从该区域酒店中选择：全季酒店(杭州黄龙店)。
- traffic_agent: success
已根据每日景点和用户约束生成交通方案：地铁/公交/步行为主，低预算优先，市内交通费用约23元。
```

### 5.3 `scripts/demo_fault.py`

命令：

```powershell
.\.venv\Scripts\python.exe scripts\demo_fault.py --timeout 180
```

完整输出：

```text
Runtime: mode=llm data=realtime

====== Get Final Task (Time elapsed: 17041.02ms) ======

Task Status: partial

Final Answer:

下面是广州3天普通预算旅行方案，整体按按用户偏好安排。

一、天气与出行约束
- 行李准备建议：
  * 证件：身份证、学生证/优惠证件（出行必备）
  * 洗漱用品：牙刷、毛巾、护肤品、防晒霜（日常所需，广州夏季紫外线强）
  * 电子产品：手机、充电器、充电宝（保持联系与记录）
  * 衣物：短袖T恤、短裤、薄外套、遮阳帽、雨伞（广州6月气温高且多雨，建议透气速干衣物并备雨具）
  * 鞋履：运动鞋、凉鞋（舒适步行及应对雨天）

二、每日景点安排
- day1: 越秀区集中游玩。景点：越秀公园、三元宫。区域：广州越秀区。
- day2: 越秀区集中游玩。景点：光孝寺、六榕寺。区域：广州越秀区。
- day3: 越秀区集中游玩。景点：城隍庙、大佛寺。区域：广州越秀区。

三、住宿建议
- 建议住宿区域：越秀区。
- 推荐酒店：7天连锁酒店(广州北京路公园前地铁站店)，类型：住宿服务;宾馆酒店;经济型连锁酒店，最近地铁：公园前站/纪念堂站，参考价格：100-300元/晚。
住宿费用小计：约200-600元。

四、城市间交通方案
- 推荐：未指定 -> 广州，高铁/动车二等座，按实际城市距离确认，单程约100-800元。
- 往返估算：约200-1600元。
城市间交通小计：约200-1600元。

五、市内交通方案
- day1:
  - 7天连锁酒店(广州北京路公园前地铁站店) -> 越秀公园: 公交，528路 -> 步行约823m，约29分钟，费用约2.0元。
  - 越秀公园 -> 三元宫: 地铁，地铁 3/1 号线等市区线路换乘，约38分钟，费用约5元。
  - 三元宫 -> 7天连锁酒店(广州北京路公园前地铁站店): 公交，293路 -> 步行约506m，约24分钟，费用约2.0元。
- day2:
  - 7天连锁酒店(广州北京路公园前地铁站店) -> 光孝寺: 公交，215路 -> 步行约807m，约24分钟，费用约2.0元。
  - 光孝寺 -> 六榕寺: 步行，步行直达，约9分钟，费用约0元。
  - 六榕寺 -> 7天连锁酒店(广州北京路公园前地铁站店): 公交，193路 -> 步行约562m，约19分钟，费用约2.0元。
- day3:
  - 7天连锁酒店(广州北京路公园前地铁站店) -> 城隍庙: 步行，步行直达，约8分钟，费用约0元。
  - 城隍庙 -> 大佛寺: 步行，步行直达，约12分钟，费用约0元。
- 预计市内交通费用：约13元。
市内交通小计：约13元。

六、费用总计
- 景点门票：0元
- 住宿费用：约200-600元
- 城市间交通：约200-1600元
- 市内交通：约13元
- 合计：约413-2213元
- 说明：以上为示例估算，实际票价、酒店价格和交通费用以出行当天平台信息为准；餐饮和购物未计入。

七、提醒
- 需要预约的热门景点请提前通过官方渠道预约，并以当天开放信息为准。
- 交通耗时、费用和住宿价格为估算结果，实际出行前以当天平台信息为准。

当前缺失信息
- 天气: 数据服务暂时不可用

说明：以上方案只基于已成功返回的数据生成，缺失模块恢复后可重新提交以补全。

Answers of Agents:
- weather_agent: error
MCP JSON-RPC error: {'code': -32003, 'message': 'Upstream MCP returned HTTP 502'}
- attraction_agent: success
已根据普通预算，为广州3天行程生成结构化景点规划
- packing_agent: success
广州6月炎热多雨，请携带透气夏装、防晒用品及雨具。
- hotel_agent: success
已先根据景点分布选择住宿区域：越秀区，再从该区域酒店中选择：7天连锁酒店(广州北京路公园前地铁站店)。
- traffic_agent: success
已根据每日景点和用户约束生成交通方案：地铁/公交/步行为主，低预算优先，市内交通费用约13元。
```

### 5.4 `scripts/demo_mcp_delay.py`

命令：

```powershell
.\.venv\Scripts\python.exe scripts\demo_mcp_delay.py --timeout 180
```

完整输出：

```text
================================================================
🐌 启动 [MCP 超时故障 Demo]
目标: 验证当 MCP Server 响应极慢时，6 秒 MCP HTTP 超时能否被触发，
      并正常向 Coordinator 返回 status: error，且不影响下游工作流。
================================================================

Runtime: mode=llm data=realtime

✈️  开始向 Coordinator 提交旅行任务...
预期表现：weather_agent 会在约 6 秒后因 MCP 超时报错，返回 error。
          Coordinator 记录该错误后继续执行后续节点。

====== Get Final Task (Time elapsed: 23353.49ms) ======

Task Status: partial

Final Answer:

下面是从上海到杭州的3天普通预算旅行方案，整体按公共交通优先安排。

一、天气与出行约束
- 行李准备建议：
  * 证件：身份证、学生证/优惠证件（出行必备，前往西湖和灵隐寺可能需要购票或查验）
  * 洗漱用品：牙刷、毛巾、护肤品（日常所需，保持个人卫生）
  * 电子产品：手机、充电器、充电宝（保持联系与记录行程，公共交通导航需要）
  * 衣物：内衣裤、袜子、长袖上衣、薄外套、长裤、舒适步行鞋（天气信息未获取，按杭州春秋季节常态准备，建议洋葱式穿衣以应对温差及步行需求）

二、每日景点安排
- day1: 西湖区集中游玩。景点：杭州西湖风景名胜区、雷峰塔景区、西湖文化广场。区域：杭州西湖区。预计门票：雷峰塔景区:40元。
- day2: 西湖区集中游玩。景点：灵隐寺。区域：杭州西湖区。预计门票：灵隐寺:75。需提前预约：灵隐寺。
- day3: 上城区集中游玩。景点：西湖天地、河坊街景区。区域：杭州上城区。
景点门票小计：约40元。

三、住宿建议
- 建议住宿区域：西湖区。
- 推荐酒店：全季酒店(杭州黄龙店)，类型：住宿服务;宾馆酒店;经济型连锁酒店，最近地铁：龙翔桥站/凤起路站，参考价格：100-300元/晚。
住宿费用小计：约200-600元。

四、城市间交通方案
- 推荐：上海 -> 杭州，高铁二等座，约0小时36分钟，单程约100-150元。
- 往返估算：约200-300元。
- 备选：普速火车更省钱但耗时更长；飞机可能更快但价格波动较大且机场通勤成本更高。
城市间交通小计：约200-300元。

五、市内交通方案
- day1:
  - 全季酒店(杭州黄龙店) -> 杭州西湖风景名胜区: 公交，87路 -> 步行约955m，约44分钟，费用约2.0元。
  - 杭州西湖风景名胜区 -> 雷峰塔景区: 公交，87路 -> 步行约2405m，约57分钟，费用约2.0元。
  - 雷峰塔景区 -> 西湖文化广场: 地铁，地铁5号线 -> 步行约3094m -> 地铁1号线 -> 步行约480m，约74分钟，费用约3.0元。
  - 西湖文化广场 -> 全季酒店(杭州黄龙店): 地铁，地铁3号线 -> 步行约479m -> 16路 -> 步行约308m，约33分钟，费用约3.0元。
- day2:
  - 全季酒店(杭州黄龙店) -> 灵隐寺: 公交，103路 -> 步行约1567m，约42分钟，费用约3.0元。
  - 灵隐寺 -> 全季酒店(杭州黄龙店): 公交，7路 -> 步行约2069m，约47分钟，费用约2.0元。
- day3:
  - 全季酒店(杭州黄龙店) -> 西湖天地: 地铁，地铁3号线 -> 步行约1223m -> 地铁1号线 -> 步行约1071m，约53分钟，费用约3.0元。
  - 西湖天地 -> 河坊街景区: 公交，195路 -> 步行约714m，约27分钟，费用约2.0元。
- 预计市内交通费用：约20元。
市内交通小计：约20元。

六、费用总计
- 景点门票：约40元
- 住宿费用：约200-600元
- 城市间交通：约200-300元
- 市内交通：约20元
- 合计：约460-960元
- 说明：以上为示例估算，实际票价、酒店价格和交通费用以出行当天平台信息为准；餐饮和购物未计入。

七、提醒
- 需要预约的热门景点请提前通过官方渠道预约，并以当天开放信息为准。
- 交通耗时、费用和住宿价格为估算结果，实际出行前以当天平台信息为准。

当前缺失信息
- 天气: 数据服务响应超时，请稍后重试

说明：以上方案只基于已成功返回的数据生成，缺失模块恢复后可重新提交以补全。

Answers of Agents:
- weather_agent: error
MCP request failed: request timed out after 6.0s
- attraction_agent: success
已根据普通预算，为杭州3天行程生成结构化景点规划
- packing_agent: success
因无法获取具体天气数据，清单已基于杭州常规气候及三天步行游览行程生成基础通用版。
- hotel_agent: success
已先根据景点分布选择住宿区域：西湖区，再从该区域酒店中选择：全季酒店(杭州黄龙店)。
- traffic_agent: success
已根据每日景点和用户约束生成交通方案：地铁/公交/步行为主，低预算优先，市内交通费用约20元。
```

### 5.5 `scripts/demo_backup_registry.py`

命令：

```powershell
.\.venv\Scripts\python.exe scripts\demo_backup_registry.py --timeout 240
```

完整输出：

```text
================================================================
🚀 启动 [双注册中心高可用 Demo]
目标: 验证主注册中心宕机时，系统是否能自动切换到备用注册中心并完成任务。
================================================================

Runtime: mode=llm data=realtime
⏳ 等待 3 秒，让所有 Agent 向两个注册中心完成初始注册和心跳...

💥 [模拟灾难] 正在强行终止主注册中心 (pid=50644)...
💀 主注册中心已宕机！

✈️  开始向 Coordinator 提交旅行任务...
预期表现：Coordinator 在主节点不可用时切换到备用节点，任务正常执行。

====== Get Final Task (Time elapsed: 31854.94ms) ======

Task Status: completed

Final Answer:

下面是从上海到杭州的3天普通预算旅行方案，整体按公共交通优先安排。

一、天气与出行约束
- 杭州day1(2026-06-23)：小雨，气温20C-26C，建议减少长时间户外安排。
- 杭州day2(2026-06-24)：小雨，气温20C-25C，建议减少长时间户外安排。
- 杭州day3(2026-06-25)：小雨，气温22C-27C，建议减少长时间户外安排。
- 行李准备建议：
  * 证件：身份证、学生证/优惠证件（出行必备，前往灵隐寺等景点可能需核验身份）
  * 洗漱用品：牙刷、毛巾、护肤品、便携雨衣（备用）（日常所需，雨天需注意皮肤保湿与干燥）
  * 电子产品：手机、充电器、充电宝、防水手机袋（保持联系与记录行程，雨天保护电子设备）
  * 衣物：内衣裤、袜子、短袖 T 恤、薄长袖衬衫、薄外套、速干长裤、透气运动鞋（气温 20-27°C 且持续小雨，建议洋葱式穿衣；短袖应对午后高温，薄外套防早晚凉意，速干衣物应对潮湿天气）
  * 雨具：折叠雨伞、一次性雨衣、防水鞋套、防水背包罩（连续三天小雨，西湖和灵隐寺多为户外步行，需全面防雨防潮）

二、每日景点安排
- day1: 雨天室内安排。景点：灵隐寺、雷峰塔景区、西湖文化广场。区域：杭州西湖区。预计门票：灵隐寺:75；雷峰塔景区:40元。需提前预约：灵隐寺。
- day2: 雨天室内安排。景点：杭州西湖风景名胜区。区域：杭州西湖区。
- day3: 雨天室内安排。景点：西湖天地、河坊街景区。区域：杭州上城区。
景点门票小计：约40元。

三、住宿建议
- 建议住宿区域：西湖区。
- 推荐酒店：全季酒店(杭州黄龙店)，类型：住宿服务;宾馆酒店;经济型连锁酒店，最近地铁：龙翔桥站/凤起路站，参考价格：100-300元/晚。
住宿费用小计：约200-600元。

四、城市间交通方案
- 推荐：上海 -> 杭州，高铁二等座，约0小时36分钟，单程约100-150元。
- 往返估算：约200-300元。
- 备选：普速火车更省钱但耗时更长；飞机可能更快但价格波动较大且机场通勤成本更高。
城市间交通小计：约200-300元。

五、市内交通方案
- day1:
  - 全季酒店(杭州黄龙店) -> 灵隐寺: 公交，103路 -> 步行约1567m，约42分钟，费用约3.0元。
  - 灵隐寺 -> 雷峰塔景区: 公交，7路 -> 步行约1006m -> 12路区间 -> 步行约2074m，约106分钟，费用约4.0元。
  - 雷峰塔景区 -> 西湖文化广场: 地铁，地铁5号线 -> 步行约3094m -> 地铁1号线 -> 步行约480m，约74分钟，费用约3.0元。
  - 西湖文化广场 -> 全季酒店(杭州黄龙店): 地铁，地铁3号线 -> 步行约1702m，约36分钟，费用约3.0元。
- day2:
  - 全季酒店(杭州黄龙店) -> 杭州西湖风景名胜区: 公交，87路 -> 步行约955m，约44分钟，费用约2.0元。
  - 杭州西湖风景名胜区 -> 全季酒店(杭州黄龙店): 公交，194路 -> 步行约2656m，约58分钟，费用约3.0元。
- day3:
  - 全季酒店(杭州黄龙店) -> 西湖天地: 地铁，地铁3号线 -> 步行约1223m -> 地铁1号线 -> 步行约1071m，约53分钟，费用约3.0元。
  - 西湖天地 -> 河坊街景区: 公交，195路 -> 步行约714m，约27分钟，费用约2.0元。
- 预计市内交通费用：约23元。
市内交通小计：约23元。

六、费用总计
- 景点门票：约40元
- 住宿费用：约200-600元
- 城市间交通：约200-300元
- 市内交通：约23元
- 合计：约463-963元
- 说明：以上为示例估算，实际票价、酒店价格和交通费用以出行当天平台信息为准；餐饮和购物未计入。

七、提醒
- 需要预约的热门景点请提前通过官方渠道预约，并以当天开放信息为准。
- 交通耗时、费用和住宿价格为估算结果，实际出行前以当天平台信息为准。

Answers of Agents:
- weather_agent: success
已生成天气活动适配：杭州市2026-06-23小雨，气温20C-26C，适合户外[]，优先室内['day1', 'day2', 'day3']。
- attraction_agent: success
已根据普通预算，为杭州3天行程生成结构化景点规划
- packing_agent: success
杭州三天多雨微热，建议携带速干衣物、多层穿搭及全套雨具，确保雨中游览舒适。
- hotel_agent: success
已先根据景点分布选择住宿区域：西湖区，再从该区域酒店中选择：全季酒店(杭州黄龙店)。
- traffic_agent: success
已根据每日景点和用户约束生成交通方案：地铁/公交/步行为主，低预算优先，市内交通费用约23元。
```

### 5.6 `scripts/demo_ack_delay.py`

命令：

```powershell
.\.venv\Scripts\python.exe scripts\demo_ack_delay.py --timeout 180
```

完整输出：

```text
Runtime: mode=llm data=realtime

====== Get Final Task (Time elapsed: 17031.73ms) ======

Task Status: partial

Final Answer:

下面是广州3天普通预算旅行方案，整体按按用户偏好安排。

一、天气与出行约束
- 行李准备建议：
  * 衣物：短袖T恤、短裤、防晒衣、遮阳帽、凉鞋（广州夏季高温多雨，气温通常在30°C以上，建议穿着透气速干衣物并做好防晒防雨准备）
  * 洗漱用品：牙刷、毛巾、防晒霜、驱蚊液、便携雨伞（高温潮湿易出汗且蚊虫较多，需加强防晒和防虫措施）
  * 电子产品：手机、充电器、充电宝、防水袋（保持联系与记录，防水袋可应对突降暴雨）

二、每日景点安排
- day1: 越秀区集中游玩。景点：越秀公园、三元宫。区域：广州越秀区。
- day2: 越秀区集中游玩。景点：光孝寺、六榕寺。区域：广州越秀区。
- day3: 越秀区集中游玩。景点：城隍庙、大佛寺。区域：广州越秀区。

三、住宿建议
- 建议住宿区域：越秀区。
- 推荐酒店：7天连锁酒店(广州北京路公园前地铁站店)，类型：住宿服务;宾馆酒店;经济型连锁酒店，最近地铁：公园前站/纪念堂站，参考价格：100-300元/晚。
住宿费用小计：约200-600元。

四、城市间交通方案
- 推荐：未指定 -> 广州，高铁/动车二等座，按实际城市距离确认，单程约100-800元。
- 往返估算：约200-1600元。
城市间交通小计：约200-1600元。

五、市内交通方案
- day1:
  - 7天连锁酒店(广州北京路公园前地铁站店) -> 越秀公园: 公交，528路 -> 步行约823m，约29分钟，费用约2.0元。
  - 越秀公园 -> 三元宫: 地铁，地铁 3/1 号线等市区线路换乘，约38分钟，费用约5元。
  - 三元宫 -> 7天连锁酒店(广州北京路公园前地铁站店): 公交，293路 -> 步行约506m，约24分钟，费用约2.0元。
- day2:
  - 7天连锁酒店(广州北京路公园前地铁站店) -> 光孝寺: 公交，215路 -> 步行约807m，约24分钟，费用约2.0元。
  - 光孝寺 -> 六榕寺: 步行，步行直达，约9分钟，费用约0元。
  - 六榕寺 -> 7天连锁酒店(广州北京路公园前地铁站店): 公交，193路 -> 步行约562m，约19分钟，费用约2.0元。
- day3:
  - 7天连锁酒店(广州北京路公园前地铁站店) -> 城隍庙: 步行，步行直达，约8分钟，费用约0元。
  - 城隍庙 -> 大佛寺: 步行，步行直达，约12分钟，费用约0元。
- 预计市内交通费用：约13元。
市内交通小计：约13元。

六、费用总计
- 景点门票：0元
- 住宿费用：约200-600元
- 城市间交通：约200-1600元
- 市内交通：约13元
- 合计：约413-2213元
- 说明：以上为示例估算，实际票价、酒店价格和交通费用以出行当天平台信息为准；餐饮和购物未计入。

七、提醒
- 需要预约的热门景点请提前通过官方渠道预约，并以当天开放信息为准。
- 交通耗时、费用和住宿价格为估算结果，实际出行前以当天平台信息为准。

当前缺失信息
- 天气: 数据服务响应超时，请稍后重试

说明：以上方案只基于已成功返回的数据生成，缺失模块恢复后可重新提交以补全。

Answers of Agents:
- attraction_agent: success
已根据普通预算，为广州3天行程生成结构化景点规划
- packing_agent: success
广州6月炎热多雨，请携带轻薄透气衣物及防晒防雨装备。
- hotel_agent: success
已先根据景点分布选择住宿区域：越秀区，再从该区域酒店中选择：7天连锁酒店(广州北京路公园前地铁站店)。
- traffic_agent: success
已根据每日景点和用户约束生成交通方案：地铁/公交/步行为主，低预算优先，市内交通费用约13元。
- weather_agent [DISPATCH_ERROR]: timed out while reading 4 bytes
```

## 6. 结论

1. `demo_ui` 可覆盖系统启动、拓扑展示、任务提交、网络事件、报文详情和 Agent 结果查看。
2. 实时 MCP、TCP framing、单元测试均通过。
3. 正常链路、MCP 超时、Agent 派发超时、备用注册中心切换、Gateway 缓存均有可复现实测输出。
