"""Mock environment data used by the JSON-RPC MCP servers.

This file intentionally keeps mock data local and deterministic. MCP servers
expose these functions over HTTP JSON-RPC; Agents should treat MCP as a tool
service, not as local helper calls.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_CITY = "北京"
DEFAULT_DATE = "明天"

WEATHER_DATA = {
    "北京": {"city": "北京", "date": "明天", "temp": "15°C", "condition": "晴", "wind": "微风"},
    "上海": {"city": "上海", "date": "明天", "temp": "18°C", "condition": "多云", "wind": "东南风 3 级"},
    "广州": {"city": "广州", "date": "明天", "temp": "24°C", "condition": "小雨", "wind": "南风 2 级"},
    "杭州": {"city": "杭州", "date": "明天", "temp": "19°C", "condition": "多云", "wind": "东风 2 级"},
    "南京": {"city": "南京", "date": "明天", "temp": "20°C", "condition": "晴到多云", "wind": "东南风 2 级"},
    "深圳": {"city": "深圳", "date": "明天", "temp": "26°C", "condition": "多云", "wind": "南风 3 级"},
    "成都": {"city": "成都", "date": "明天", "temp": "21°C", "condition": "阴", "wind": "东风 2 级"},
    "重庆": {"city": "重庆", "date": "明天", "temp": "23°C", "condition": "阴转小雨", "wind": "西南风 2 级"},
    "武汉": {"city": "武汉", "date": "明天", "temp": "22°C", "condition": "阵雨", "wind": "东风 3 级"},
    "西安": {"city": "西安", "date": "明天", "temp": "19°C", "condition": "晴", "wind": "西风 2 级"},
    "苏州": {"city": "苏州", "date": "明天", "temp": "20°C", "condition": "多云", "wind": "东风 2 级"},
    "天津": {"city": "天津", "date": "明天", "temp": "17°C", "condition": "晴", "wind": "北风 2 级"},
}

TRANSPORT_DATA = {
    "北京": {"city": "北京", "route": "地铁 4 号线 -> 2 号线", "status": "早高峰局部拥堵", "duration": "约 45 分钟"},
    "上海": {"city": "上海", "route": "地铁 2 号线 -> 10 号线", "status": "主干道通行正常", "duration": "约 38 分钟"},
    "广州": {"city": "广州", "route": "地铁 3 号线 -> 1 号线", "status": "雨天车速偏慢", "duration": "约 50 分钟"},
    "杭州": {"city": "杭州", "route": "地铁 1 号线/2 号线/5 号线按景区就近换乘", "status": "主城区通行正常，西湖周边建议步行接驳", "duration": "约 30-50 分钟"},
    "南京": {"city": "南京", "route": "地铁 1 号线/2 号线/3 号线按景点换乘", "status": "主干线路通行正常，夫子庙秦淮河周边建议步行", "duration": "约 30-55 分钟"},
    "深圳": {"city": "深圳", "route": "地铁 1 号线/2 号线/4 号线按景点换乘", "status": "主干线路通行正常", "duration": "约 25-45 分钟"},
    "成都": {"city": "成都", "route": "地铁 1 号线/2 号线/3 号线按景点换乘", "status": "主干线路通行正常", "duration": "约 30-50 分钟"},
    "重庆": {"city": "重庆", "route": "地铁 1 号线/3 号线/6 号线按景点换乘", "status": "过江大桥和隧道车流较大", "duration": "约 35-60 分钟"},
    "武汉": {"city": "武汉", "route": "地铁 2 号线/4 号线/8 号线按景点换乘", "status": "早晚高峰长江隧道拥堵", "duration": "约 30-55 分钟"},
    "西安": {"city": "西安", "route": "地铁 2 号线/3 号线/4 号线按景点换乘", "status": "钟楼周边车流较大，建议地铁出行", "duration": "约 25-45 分钟"},
    "苏州": {"city": "苏州", "route": "地铁 1 号线/2 号线/4 号线按景点换乘", "status": "古城区道路较窄，建议地铁或步行", "duration": "约 30-50 分钟"},
    "天津": {"city": "天津", "route": "地铁 1 号线/3 号线/9 号线按景点换乘", "status": "主干线路通行正常", "duration": "约 25-45 分钟"},
}

ATTRACTION_DATA = {
    "北京": [
        {
            "name": "天安门广场",
            "area": "天安门-故宫区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": True,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "天安门东/天安门西",
            "tags": ["经典景点", "免费", "地标", "必去"],
        },
        {
            "name": "故宫",
            "area": "天安门-故宫区域",
            "ticket": "40-60元",
            "duration": "3-4小时",
            "open_time": "08:30-17:00",
            "reservation_required": True,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "天安门东/天安门西",
            "tags": ["经典景点", "历史", "必去"],
        },
        {
            "name": "国家博物馆",
            "area": "天安门-故宫区域",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "09:00-17:00",
            "reservation_required": True,
            "indoor_or_outdoor": "indoor",
            "nearest_subway": "天安门东",
            "tags": ["室内", "雨天备选", "免费", "博物馆"],
        },
        {
            "name": "景山公园",
            "area": "天安门-故宫区域",
            "ticket": "2-10元",
            "duration": "1-2小时",
            "open_time": "06:30-21:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "中国美术馆/南锣鼓巷",
            "tags": ["低价", "观景", "户外"],
        },
        {
            "name": "前门",
            "area": "天坛-前门区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "前门",
            "tags": ["免费", "老北京街区", "公共交通方便"],
        },
        {
            "name": "大栅栏",
            "area": "天坛-前门区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "前门",
            "tags": ["免费", "老北京街区", "低预算"],
        },
        {
            "name": "天坛",
            "area": "天坛-前门区域",
            "ticket": "15-34元",
            "duration": "2-3小时",
            "open_time": "06:00-22:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "天坛东门",
            "tags": ["经典景点", "低价", "户外"],
        },
        {
            "name": "什刹海",
            "area": "什刹海-南锣鼓巷区域",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "什刹海",
            "tags": ["免费", "休闲", "低预算"],
        },
        {
            "name": "南锣鼓巷",
            "area": "什刹海-南锣鼓巷区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "南锣鼓巷",
            "tags": ["免费", "街区", "低预算"],
        },
        {
            "name": "圆明园",
            "area": "海淀西北区域",
            "ticket": "10-25元",
            "duration": "2-3小时",
            "open_time": "07:00-19:30",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "圆明园",
            "tags": ["低价", "户外", "历史"],
        },
        {
            "name": "颐和园",
            "area": "海淀西北区域",
            "ticket": "20-60元",
            "duration": "3-4小时",
            "open_time": "06:00-20:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "北宫门",
            "tags": ["经典景点", "户外", "园林"],
        },
    ],
    "上海": [
        {
            "name": "外滩",
            "area": "黄浦江沿线",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "南京东路",
            "tags": ["免费", "经典景点", "地标"],
        },
        {
            "name": "豫园",
            "area": "豫园-老城厢区域",
            "ticket": "30-40元",
            "duration": "2-3小时",
            "open_time": "09:00-16:30",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "豫园",
            "tags": ["经典景点", "历史", "园林", "必去"],
        },
        {
            "name": "南京路步行街",
            "area": "人民广场-南京路区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "人民广场/南京东路",
            "tags": ["免费", "购物", "地标", "公共交通方便"],
        },
        {
            "name": "上海博物馆",
            "area": "人民广场-南京路区域",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "09:00-17:00",
            "reservation_required": True,
            "indoor_or_outdoor": "indoor",
            "nearest_subway": "人民广场",
            "tags": ["室内", "雨天备选", "免费", "博物馆", "必去"],
        },
        {
            "name": "陆家嘴",
            "area": "浦东陆家嘴区域",
            "ticket": "免费(外观)",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "陆家嘴",
            "tags": ["免费", "地标", "摩天大楼", "现代"],
        },
        {
            "name": "田子坊",
            "area": "打浦桥-田子坊区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "打浦桥",
            "tags": ["免费", "艺术街区", "美食", "低预算"],
        },
    ],
    "广州": [
        {
            "name": "广州塔",
            "area": "珠江新城-广州塔区域",
            "ticket": "外观免费，登塔另收费",
            "duration": "1-2小时",
            "open_time": "09:30-22:30",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "广州塔",
            "tags": ["地标", "经典景点"],
        },
        {
            "name": "沙面岛",
            "area": "沙面-荔湾区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "文化公园/黄沙",
            "tags": ["免费", "历史街区", "欧式建筑", "休闲"],
        },
        {
            "name": "永庆坊",
            "area": "荔湾-永庆坊区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "黄沙/如意坊",
            "tags": ["免费", "历史街区", "文化", "网红"],
        },
        {
            "name": "陈家祠",
            "area": "荔湾-陈家祠区域",
            "ticket": "10元",
            "duration": "1-2小时",
            "open_time": "09:00-17:30",
            "reservation_required": False,
            "indoor_or_outdoor": "indoor",
            "nearest_subway": "陈家祠",
            "tags": ["历史", "建筑", "艺术", "低价", "室内"],
        },
        {
            "name": "越秀公园",
            "area": "越秀公园区域",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "06:00-22:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "越秀公园/纪念堂",
            "tags": ["免费", "自然", "经典景点", "户外"],
        },
        {
            "name": "北京路步行街",
            "area": "北京路-天河城区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "北京路/公园前",
            "tags": ["免费", "购物", "美食", "公共交通方便"],
        },
    ],
    "杭州": [
        {
            "name": "西湖",
            "area": "湖滨-西湖边",
            "ticket": "免费",
            "duration": "3-4小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "龙翔桥/凤起路",
            "tags": ["经典景点", "免费", "地标", "必去", "公共交通方便"],
        },
        {
            "name": "灵隐寺",
            "area": "灵隐-西湖西线",
            "ticket": "45-75元",
            "duration": "2-3小时",
            "open_time": "07:00-18:00",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "黄龙体育中心换乘公交",
            "tags": ["经典景点", "历史", "寺庙", "mixed"],
        },
        {
            "name": "河坊街",
            "area": "吴山-河坊街区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "定安路",
            "tags": ["免费", "街区", "低预算", "公共交通方便"],
        },
        {
            "name": "西溪湿地",
            "area": "西溪湿地区域",
            "ticket": "70-80元",
            "duration": "3-4小时",
            "open_time": "08:00-17:30",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "西溪湿地南",
            "tags": ["自然", "经典景点", "户外"],
        },
        {
            "name": "京杭大运河",
            "area": "拱宸桥-运河区域",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "拱宸桥东",
            "tags": ["免费", "历史", "低预算", "公共交通方便"],
        },
        {
            "name": "雷峰塔",
            "area": "湖滨-西湖边",
            "ticket": "40元",
            "duration": "1-2小时",
            "open_time": "08:00-20:00",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "龙翔桥换乘公交",
            "tags": ["经典景点", "历史", "西湖周边"],
        },
    ],
    "南京": [
        {
            "name": "中山陵",
            "area": "钟山风景区",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "08:30-17:00",
            "reservation_required": True,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "苜蓿园换乘景区交通",
            "tags": ["经典景点", "免费", "历史", "必去"],
        },
        {
            "name": "夫子庙",
            "area": "夫子庙-秦淮河区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "夫子庙",
            "tags": ["经典景点", "免费", "街区", "公共交通方便"],
        },
        {
            "name": "秦淮河",
            "area": "夫子庙-秦淮河区域",
            "ticket": "河岸免费，游船另收费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "夫子庙/武定门",
            "tags": ["经典景点", "夜景", "低预算"],
        },
        {
            "name": "明孝陵",
            "area": "钟山风景区",
            "ticket": "70元",
            "duration": "2-3小时",
            "open_time": "06:30-18:30",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "苜蓿园",
            "tags": ["经典景点", "历史", "户外"],
        },
        {
            "name": "南京博物院",
            "area": "中山东路-博物院区域",
            "ticket": "免费",
            "duration": "2-4小时",
            "open_time": "09:00-17:00",
            "reservation_required": True,
            "indoor_or_outdoor": "indoor",
            "nearest_subway": "博物院周边地铁站",
            "tags": ["室内", "雨天备选", "免费", "博物馆"],
        },
        {
            "name": "总统府",
            "area": "新街口-总统府区域",
            "ticket": "35元",
            "duration": "1-2小时",
            "open_time": "08:30-17:00",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "大行宫",
            "tags": ["经典景点", "历史", "公共交通方便"],
        },
    ],
    "深圳": [
        {
            "name": "世界之窗",
            "area": "南山区",
            "ticket": "200元",
            "duration": "3-4小时",
            "open_time": "09:00-22:30",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "世界之窗",
            "tags": ["主题公园", "热门", "地标"],
        },
        {
            "name": "莲花山公园",
            "area": "福田区",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "06:00-23:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "莲花西/少年宫",
            "tags": ["免费", "自然", "公园", "户外"],
        },
        {
            "name": "深圳湾公园",
            "area": "南山区",
            "ticket": "免费",
            "duration": "1-3小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "深圳湾公园",
            "tags": ["免费", "海滨", "休闲", "户外"],
        },
        {
            "name": "大鹏所城",
            "area": "大鹏新区",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "09:00-17:30",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "需转乘公交至大鹏",
            "tags": ["免费", "历史", "古迹"],
        },
        {
            "name": "东部华侨城",
            "area": "盐田区",
            "ticket": "180-200元",
            "duration": "4-6小时",
            "open_time": "09:30-18:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "大梅沙换乘接驳巴士",
            "tags": ["主题公园", "自然", "热门"],
        },
        {
            "name": "深圳博物馆",
            "area": "福田区",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "10:00-18:00",
            "reservation_required": True,
            "indoor_or_outdoor": "indoor",
            "nearest_subway": "市民中心",
            "tags": ["室内", "雨天备选", "免费", "博物馆"],
        },
    ],
    "成都": [
        {
            "name": "宽窄巷子",
            "area": "青羊区",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "宽窄巷子",
            "tags": ["免费", "历史街区", "经典景点", "公共交通方便"],
        },
        {
            "name": "武侯祠",
            "area": "武侯区",
            "ticket": "50元",
            "duration": "2-3小时",
            "open_time": "08:00-20:00",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "高升桥",
            "tags": ["历史", "经典景点", "古迹"],
        },
        {
            "name": "杜甫草堂",
            "area": "青羊区",
            "ticket": "47元",
            "duration": "1-2小时",
            "open_time": "08:00-19:00",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "草堂北路",
            "tags": ["历史", "文化", "园林"],
        },
        {
            "name": "大熊猫繁育研究基地",
            "area": "成华区",
            "ticket": "55元",
            "duration": "2-4小时",
            "open_time": "07:30-17:30",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "熊猫大道",
            "tags": ["动物园", "必去", "经典景点", "户外"],
        },
        {
            "name": "青城山",
            "area": "都江堰市",
            "ticket": "80元",
            "duration": "4-6小时",
            "open_time": "08:00-18:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "都江堰站换乘公交",
            "tags": ["自然", "登山", "世界遗产"],
        },
        {
            "name": "锦里",
            "area": "武侯区",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "高升桥",
            "tags": ["免费", "美食", "街区", "公共交通方便"],
        },
    ],
    "重庆": [
        {
            "name": "洪崖洞",
            "area": "渝中区",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "小什字",
            "tags": ["免费", "地标", "夜景", "必去"],
        },
        {
            "name": "解放碑",
            "area": "渝中区",
            "ticket": "免费",
            "duration": "0.5-1小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "较场口/小什字",
            "tags": ["免费", "地标", "经典景点"],
        },
        {
            "name": "磁器口古镇",
            "area": "沙坪坝区",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "磁器口",
            "tags": ["免费", "历史街区", "美食", "公共交通方便"],
        },
        {
            "name": "长江索道",
            "area": "渝中区-南岸区",
            "ticket": "单程20元",
            "duration": "0.5-1小时",
            "open_time": "07:30-22:30",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "小什字",
            "tags": ["体验", "地标", "经典景点"],
        },
        {
            "name": "武隆天生三桥",
            "area": "武隆区",
            "ticket": "95元",
            "duration": "3-4小时",
            "open_time": "08:00-17:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "需从重庆市区乘车前往",
            "tags": ["自然", "世界遗产", "户外"],
        },
        {
            "name": "李子坝轻轨穿楼",
            "area": "渝中区",
            "ticket": "免费(观景台)",
            "duration": "0.5小时",
            "open_time": "观景台全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "李子坝",
            "tags": ["免费", "网红", "体验"],
        },
    ],
    "武汉": [
        {
            "name": "黄鹤楼",
            "area": "武昌区",
            "ticket": "70元",
            "duration": "1-2小时",
            "open_time": "08:00-18:00",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "司门口黄鹤楼",
            "tags": ["经典景点", "历史", "地标"],
        },
        {
            "name": "东湖风景区",
            "area": "武昌区",
            "ticket": "免费",
            "duration": "3-5小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "梨园/东湖路",
            "tags": ["免费", "自然", "经典景点", "户外"],
        },
        {
            "name": "武汉长江大桥",
            "area": "武昌区-汉阳区",
            "ticket": "免费",
            "duration": "0.5-1小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "司门口黄鹤楼/汉阳",
            "tags": ["免费", "地标", "经典景点"],
        },
        {
            "name": "湖北省博物馆",
            "area": "武昌区",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "09:00-17:00",
            "reservation_required": True,
            "indoor_or_outdoor": "indoor",
            "nearest_subway": "东湖路/省博物馆",
            "tags": ["室内", "雨天备选", "免费", "博物馆"],
        },
        {
            "name": "归元禅寺",
            "area": "汉阳区",
            "ticket": "20元",
            "duration": "1-2小时",
            "open_time": "08:00-17:00",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "钟家村",
            "tags": ["寺庙", "历史", "低价"],
        },
        {
            "name": "户部巷",
            "area": "武昌区",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "司门口黄鹤楼",
            "tags": ["免费", "美食", "街区", "低价"],
        },
    ],
    "西安": [
        {
            "name": "秦始皇兵马俑博物馆",
            "area": "临潼区",
            "ticket": "120元",
            "duration": "2-3小时",
            "open_time": "08:30-17:30",
            "reservation_required": False,
            "indoor_or_outdoor": "indoor",
            "nearest_subway": "需在西安站换乘公交/大巴",
            "tags": ["经典景点", "历史", "必去", "世界遗产"],
        },
        {
            "name": "大雁塔",
            "area": "雁塔区",
            "ticket": "40-85元",
            "duration": "1-2小时",
            "open_time": "08:00-21:00",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "大雁塔",
            "tags": ["经典景点", "历史", "地铁方便"],
        },
        {
            "name": "西安城墙",
            "area": "碑林区",
            "ticket": "54元",
            "duration": "2-4小时",
            "open_time": "08:00-22:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "永宁门",
            "tags": ["经典景点", "历史", "地标", "户外"],
        },
        {
            "name": "回民街",
            "area": "莲湖区",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "钟楼",
            "tags": ["免费", "美食", "街区", "公共交通方便"],
        },
        {
            "name": "陕西历史博物馆",
            "area": "雁塔区",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "08:30-18:00",
            "reservation_required": True,
            "indoor_or_outdoor": "indoor",
            "nearest_subway": "小寨",
            "tags": ["室内", "雨天备选", "免费", "博物馆", "必去"],
        },
        {
            "name": "钟楼",
            "area": "莲湖区",
            "ticket": "30元",
            "duration": "0.5-1小时",
            "open_time": "08:00-22:00",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "钟楼",
            "tags": ["地标", "经典景点", "低价"],
        },
    ],
    "苏州": [
        {
            "name": "拙政园",
            "area": "姑苏区",
            "ticket": "80元",
            "duration": "2-3小时",
            "open_time": "07:30-17:30",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "北寺塔",
            "tags": ["经典景点", "园林", "世界遗产", "必去"],
        },
        {
            "name": "周庄古镇",
            "area": "昆山市",
            "ticket": "100元",
            "duration": "3-5小时",
            "open_time": "08:00-20:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "需从市区换乘公交到达",
            "tags": ["古镇", "经典景点", "水乡"],
        },
        {
            "name": "虎丘",
            "area": "姑苏区",
            "ticket": "70元",
            "duration": "2-3小时",
            "open_time": "07:30-17:30",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "石路/山塘街",
            "tags": ["经典景点", "历史", "园林"],
        },
        {
            "name": "苏州博物馆",
            "area": "姑苏区",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "09:00-17:00",
            "reservation_required": True,
            "indoor_or_outdoor": "indoor",
            "nearest_subway": "北寺塔",
            "tags": ["室内", "雨天备选", "免费", "博物馆", "必去"],
        },
        {
            "name": "平江路",
            "area": "姑苏区",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "相门",
            "tags": ["免费", "历史街区", "低预算", "公共交通方便"],
        },
        {
            "name": "金鸡湖",
            "area": "吴中区/工业园区",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "时代广场/文化博览中心",
            "tags": ["免费", "现代", "休闲", "户外"],
        },
    ],
    "天津": [
        {
            "name": "天津之眼",
            "area": "河北区",
            "ticket": "70元",
            "duration": "1小时",
            "open_time": "09:00-22:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "天津之眼周边",
            "tags": ["地标", "经典景点", "摩天轮"],
        },
        {
            "name": "五大道",
            "area": "和平区",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "小白楼",
            "tags": ["免费", "历史街区", "经典景点", "户外"],
        },
        {
            "name": "古文化街",
            "area": "南开区",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "09:00-18:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "东南角",
            "tags": ["免费", "历史街区", "美食", "公共交通方便"],
        },
        {
            "name": "天津博物馆",
            "area": "河西区",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "09:00-16:30",
            "reservation_required": True,
            "indoor_or_outdoor": "indoor",
            "nearest_subway": "文化中心",
            "tags": ["室内", "雨天备选", "免费", "博物馆"],
        },
        {
            "name": "瓷房子",
            "area": "和平区",
            "ticket": "50元",
            "duration": "0.5-1小时",
            "open_time": "09:00-18:00",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "营口道",
            "tags": ["建筑", "网红", "艺术"],
        },
        {
            "name": "意大利风情区",
            "area": "河北区",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "建国道/天津站",
            "tags": ["免费", "历史街区", "欧式建筑"],
        },
    ],
}


def get_weather(city: str = DEFAULT_CITY, date: str = DEFAULT_DATE, days: int = 1, **_: Any) -> dict[str, Any]:
    """Return mock weather data. When days > 1, returns forecast_days array."""
    from datetime import date as date_type, datetime, timedelta
    normalized_city = (city or DEFAULT_CITY).strip()
    data = _lookup(WEATHER_DATA, normalized_city)

    # Parse the requested start date
    start_date = date or DEFAULT_DATE
    try:
        base_date = datetime.strptime(start_date[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        base_date = date_type.today() + timedelta(days=1)

    day_count = max(1, int(days or 1))

    # Build multi-day forecast from the base data
    base_condition = str(data.get("condition", "晴"))
    base_temp_str = str(data.get("temp", "15°C"))
    base_temp = int(__import__("re").findall(r"-?\d+", base_temp_str)[0]) if __import__("re").findall(r"-?\d+", base_temp_str) else 15
    base_wind = str(data.get("wind", "微风"))

    conditions_pool = ["晴", "多云", "阴", "小雨", "晴间多云", "晴", "多云转晴"]
    forecast_days = []
    for i in range(day_count):
        d = base_date + timedelta(days=i)
        temp_offset = (i * 3) % 7 - 3  # small variation across days
        forecast_days.append({
            "date": d.isoformat(),
            "temp_max": f"{base_temp + temp_offset + 5}°C",
            "temp_min": f"{base_temp + temp_offset - 3}°C",
            "condition": conditions_pool[i % len(conditions_pool)],
            "wind": base_wind,
            "weather_code": 0 if "晴" in conditions_pool[i % len(conditions_pool)] else (2 if "多云" in conditions_pool[i % len(conditions_pool)] else 3),
        })

    result = {
        "city": normalized_city,
        "date": start_date,
        "temp": data.get("temp", "15°C"),
        "condition": base_condition,
        "wind": base_wind,
        "requested_city": normalized_city,
        "forecast_days": forecast_days,
    }
    return result

def get_packing_list(city: str = DEFAULT_CITY, days: int = 3, temperature: str = "", condition: str = "", **_: Any) -> dict[str, Any]:
    """Return mock packing list based on destination and weather."""
    normalized_city = (city or DEFAULT_CITY).strip()
    
    base_items = [
        {"category": "证件", "items": ["身份证", "学生证/优惠证件"], "reason": "出行必备"},
        {"category": "洗漱用品", "items": ["牙刷", "毛巾", "护肤品"], "reason": "日常所需"},
        {"category": "电子产品", "items": ["手机", "充电器", "充电宝"], "reason": "保持联系与记录"}
    ]
    
    clothing_items = ["内衣裤", "袜子"]
    if "冷" in condition or "雪" in condition or (temperature and any(int(t) < 10 for t in __import__("re").findall(r"-?\d+", temperature))):
        clothing_items.extend(["羽绒服", "保暖内衣", "围巾", "手套"])
        clothing_reason = "天气寒冷，需注意保暖"
    elif "热" in condition or (temperature and any(int(t) > 28 for t in __import__("re").findall(r"-?\d+", temperature))):
        clothing_items.extend(["短袖", "短裤", "防晒衣"])
        clothing_reason = "天气炎热，需透气和防晒"
    else:
        clothing_items.extend(["长袖", "薄外套", "长裤"])
        clothing_reason = "气温适中，建议洋葱式穿衣"
        
    base_items.append({"category": "衣物", "items": clothing_items, "reason": clothing_reason})
    
    if "雨" in condition:
        base_items.append({"category": "雨具", "items": ["雨伞", "雨衣", "防水鞋套"], "reason": "预报有雨"})
    elif "晴" in condition or "太阳" in condition:
        base_items.append({"category": "防晒", "items": ["太阳伞", "墨镜", "防晒霜"], "reason": "预报晴天，注意防晒"})
        
    return {
        "city": normalized_city,
        "days": days,
        "weather_condition_used": condition,
        "temperature_used": temperature,
        "packing_list": base_items
    }


def get_transport(city: str = DEFAULT_CITY, date: str = DEFAULT_DATE, **_: Any) -> dict[str, Any]:
    # 获取城市交通概况数据
    data = _lookup(TRANSPORT_DATA, city)
    data["date"] = date or DEFAULT_DATE
    return data


def get_traffic(city: str = DEFAULT_CITY, date: str = DEFAULT_DATE, **kwargs: Any) -> dict[str, Any]:
    """Backward-compatible alias for older local config/tests."""
    return get_transport(city=city, date=date, **kwargs)


def search_attractions(
    city: str = DEFAULT_CITY,
    days: int = 3,
    budget_level: str = "normal",
    must_visit: list[str] | None = None,
    preferences: list[str] | None = None,
    requested_fields: list[str] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Return mock attraction data, ranked by must-visit, budget and preference hints."""
    normalized_city = (city or "").strip()
    spots: list[dict[str, Any]] = deepcopy(ATTRACTION_DATA.get(normalized_city) or [])

    must_visit = must_visit or []
    preferences = preferences or []
    requested_fields = requested_fields or []

    def score(spot: dict[str, Any]) -> int:
        # 根据必去景点、预算和偏好给景点排序打分
        value = 0
        name = str(spot.get("name", ""))
        area = str(spot.get("area", ""))
        tags = spot.get("tags", [])
        ticket = str(spot.get("ticket", ""))

        for item in must_visit:
            item = str(item)
            if item and (item in name or name in item):
                value += 100

        if budget_level == "low":
            if "免费" in ticket:
                value += 30
            if "低价" in tags:
                value += 20

        for pref in preferences:
            pref = str(pref)
            if pref and (pref in tags or pref in name or pref in area):
                value += 10

        if "经典景点" in tags:
            value += 5
        return value

    spots.sort(key=score, reverse=True)

    if requested_fields:
        keep = set(requested_fields) | {"name", "area", "tags"}
        spots = [{key: value for key, value in spot.items() if key in keep} for spot in spots]

    return {
        "city": normalized_city,
        "requested_city": normalized_city,
        "fallback_used": normalized_city not in ATTRACTION_DATA,
        "days": days,
        "budget_level": budget_level,
        "must_visit": must_visit,
        "preferences": preferences,
        "spots": spots,
    }


def get_route(
    city: str = DEFAULT_CITY,
    origin: str = "",
    destination: str = "",
    preference: str = "public_transport",
    **_: Any,
) -> dict[str, Any]:
    """Return mock candidate routes between two attractions."""
    normalized_city = (city or DEFAULT_CITY).strip()
    origin = (origin or "出发地").strip()
    destination = (destination or "目的地").strip()
    same_area = _same_area(normalized_city, origin, destination)

    if same_area:
        candidates = [
            {
                "mode": "walk",
                "route": "步行前往",
                "duration_minutes": 12,
                "cost_yuan": 0,
                "walk_minutes": 12,
                "transfers": 0,
                "note": "同一区域景点，步行成本最低",
            },
            {
                "mode": "taxi",
                "route": "打车短途直达",
                "duration_minutes": 8,
                "cost_yuan": 18,
                "walk_minutes": 2,
                "transfers": 0,
                "note": "最快但不符合低预算优先",
            },
        ]
    else:
        candidates = [
            {
                "mode": "subway",
                "route": _generic_subway_route(normalized_city, origin, destination),
                "duration_minutes": 38,
                "cost_yuan": 5,
                "walk_minutes": 12,
                "transfers": 1,
                "note": "公共交通优先，费用低且稳定",
            },
            {
                "mode": "bus",
                "route": "公交换乘方案",
                "duration_minutes": 52,
                "cost_yuan": 2,
                "walk_minutes": 15,
                "transfers": 1,
                "note": "费用最低，但时间更长",
            },
            {
                "mode": "taxi",
                "route": "打车直达",
                "duration_minutes": 28,
                "cost_yuan": 42,
                "walk_minutes": 3,
                "transfers": 0,
                "note": "最快但费用较高",
            },
        ]

    return {
        "city": normalized_city,
        "requested_city": normalized_city,
        "fallback_used": normalized_city not in ATTRACTION_DATA,
        "origin": origin,
        "destination": destination,
        "preference": preference,
        "same_area": same_area,
        "candidates": candidates,
    }


def get_routes(
    city: str = DEFAULT_CITY,
    segments: list[dict[str, Any]] | None = None,
    preference: str = "public_transport",
    **_: Any,
) -> dict[str, Any]:
    # 批量获取多段路线方案
    segments = segments or []
    routes = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        routes.append(
            get_route(
                city=city,
                origin=str(segment.get("origin", "")),
                destination=str(segment.get("destination", "")),
                preference=preference,
            )
        )
    return {"city": city, "preference": preference, "routes": routes}


def _lookup(dataset: dict[str, dict[str, Any]], city: str) -> dict[str, Any]:
    # 从数据集中查找城市数据，缺失则返回空（不再回退到北京）
    normalized_city = (city or "").strip()
    data = dataset.get(normalized_city)
    if isinstance(data, dict):
        return data
    return {"_mock_unavailable": True}
    result = deepcopy(data)
    result["requested_city"] = normalized_city
    result["fallback_used"] = normalized_city not in dataset
    return result


def _spot_area(city: str, spot_name: str) -> str | None:
    # 查找景点或酒店所属的区域名称
    spots = ATTRACTION_DATA.get(city, [])
    for spot in spots:
        name = str(spot.get("name", ""))
        if spot_name == name or spot_name in name or name in spot_name:
            return str(spot.get("area", ""))

    # Hotel names can also be route endpoints after Hotel Agent is inserted
    # before Traffic Agent. HOTEL_DATA is defined later in this module; the
    # lookup happens at runtime after module initialization, so globals() is safe.
    hotel_data = globals().get("HOTEL_DATA", {})
    hotels = hotel_data.get(city, []) if isinstance(hotel_data, dict) else []
    for hotel in hotels:
        if not isinstance(hotel, dict):
            continue
        name = str(hotel.get("name", ""))
        area = str(hotel.get("area", ""))
        if spot_name == name or spot_name in name or name in spot_name:
            return area
    return None


def _same_area(city: str, origin: str, destination: str) -> bool:
    # 判断两个地点是否属于同一区域
    origin_area = _spot_area(city, origin)
    dest_area = _spot_area(city, destination)
    return bool(origin_area and dest_area and origin_area == dest_area)


def _generic_subway_route(city: str, origin: str, destination: str) -> str:
    # 根据城市生成通用的地铁换乘描述
    if city == "北京":
        return "地铁为主，按地图选择最近站点换乘"
    if city == "上海":
        return "地铁 2/10 号线等市区线路换乘"
    if city == "广州":
        return "地铁 3/1 号线等市区线路换乘"
    if city == "杭州":
        return "地铁 1/2/5 号线结合公交或步行接驳"
    if city == "南京":
        return "地铁 1/2/3 号线结合景区步行接驳"
    if city == "深圳":
        return "地铁 1/2/4 号线等市区线路换乘"
    if city == "成都":
        return "地铁 1/2/3 号线等市区线路换乘"
    if city == "重庆":
        return "地铁 1/3/6 号线结合过江索道接驳"
    if city == "武汉":
        return "地铁 2/4/8 号线结合长江大桥步行接驳"
    if city == "西安":
        return "地铁 2/3/4 号线等市区线路换乘"
    if city == "苏州":
        return "地铁 1/2/4 号线结合古城区步行接驳"
    if city == "天津":
        return "地铁 1/3/9 号线等市区线路换乘"
    return "城市轨道交通换乘"


HOTEL_DATA = {
    "北京": [
        # ── 奢华/高端 (高预算) ──
        {
            "name": "北京王府半岛酒店",
            "area": "王府井-故宫区域",
            "price_per_night": 1800,
            "type": "奢华五星酒店",
            "nearest_subway": "灯市口/金鱼胡同",
            "tags": ["奢华", "近故宫", "高品质", "管家服务"],
            "pros": ["步行可达故宫、王府井", "房间宽敞舒适", "服务和设施一流", "适合追求高品质住宿"],
            "cons": ["价格较高"],
        },
        {
            "name": "北京国贸大酒店",
            "area": "CBD-国贸区域",
            "price_per_night": 1500,
            "type": "豪华五星酒店",
            "nearest_subway": "国贸",
            "tags": ["高端商务", "天际线景观", "行政酒廊", "舒适优先"],
            "pros": ["京城天际线景观", "行政酒廊待遇", "房间设施顶级", "适合商务和度假"],
            "cons": ["距离故宫等景点需打车或地铁约20分钟"],
        },
        {
            "name": "北京璞瑄酒店",
            "area": "天安门-故宫区域",
            "price_per_night": 2200,
            "type": "奢华设计师酒店",
            "nearest_subway": "中国美术馆/东四",
            "tags": ["设计师酒店", "近故宫景山", "低调奢华", "艺术氛围"],
            "pros": ["紧邻中国美术馆和故宫", "设计感极强", "闹中取静", "适合追求独特体验"],
            "cons": ["价格较高", "偏好传统酒店风格者可能不适应"],
        },
        # ── 中端舒适型 ──
        {
            "name": "北京诺富特和平宾馆",
            "area": "王府井-灯市口区域",
            "price_per_night": 580,
            "type": "舒适型酒店",
            "nearest_subway": "灯市口",
            "tags": ["舒适", "近王府井", "性价比", "国际品牌"],
            "pros": ["步行可达王府井步行街", "国际品牌标准", "早餐品质好", "性价比高"],
            "cons": ["房间面积中等"],
        },
        {
            "name": "北京西苑饭店",
            "area": "西直门-动物园区域",
            "price_per_night": 450,
            "type": "舒适型酒店",
            "nearest_subway": "动物园/西直门",
            "tags": ["舒适", "近动物园", "园林式", "家庭友好"],
            "pros": ["园林式环境", "靠近动物园和天文馆", "适合家庭出行", "价格适中"],
            "cons": ["距离故宫核心区需地铁约20分钟"],
        },
        # ── 经济型 (低预算) ──
        {
            "name": "前门轻居酒店",
            "area": "天坛-前门区域",
            "price_per_night": 230,
            "type": "经济型酒店",
            "nearest_subway": "前门",
            "tags": ["低预算", "地铁方便", "近天安门", "老城核心"],
            "pros": ["靠近前门、大栅栏和天安门", "步行和地铁都方便", "适合第一次来北京"],
            "cons": ["核心区房间可能偏小"],
        },
        {
            "name": "东直门便捷酒店",
            "area": "东直门-雍和宫区域",
            "price_per_night": 260,
            "type": "经济型酒店",
            "nearest_subway": "东直门",
            "tags": ["交通枢纽", "地铁方便", "机场线方便"],
            "pros": ["地铁线路多", "去机场方便", "换乘便利"],
            "cons": ["离天安门和故宫不是最近"],
        },
        {
            "name": "王府井青年旅舍",
            "area": "天安门-故宫区域",
            "price_per_night": 160,
            "type": "青旅/床位",
            "nearest_subway": "王府井/金鱼胡同",
            "tags": ["低预算", "近故宫", "公共交通方便", "性价比"],
            "pros": ["靠近故宫和天安门", "价格低", "适合低预算"],
            "cons": ["私密性较弱", "舒适度一般"],
        },
        {
            "name": "西直门地铁站酒店",
            "area": "西直门-动物园区域",
            "price_per_night": 240,
            "type": "经济型酒店",
            "nearest_subway": "西直门",
            "tags": ["地铁枢纽", "去海淀方便", "公共交通方便"],
            "pros": ["去颐和园、圆明园方向方便", "多线路换乘"],
            "cons": ["距离天安门核心区略远"],
        },
        {
            "name": "海淀圆明园学生公寓式酒店",
            "area": "海淀西北区域",
            "price_per_night": 210,
            "type": "公寓式酒店",
            "nearest_subway": "圆明园",
            "tags": ["低预算", "近圆明园", "安静"],
            "pros": ["去圆明园、颐和园方便", "价格相对低"],
            "cons": ["去天安门和故宫较远"],
        },
    ],
    "上海": [
        # ── 奢华/高端 ──
        {
            "name": "上海和平饭店",
            "area": "外滩-南京东路区域",
            "price_per_night": 2000,
            "type": "奢华五星酒店",
            "nearest_subway": "南京东路",
            "tags": ["奢华", "历史建筑", "外滩景观", "地标"],
            "pros": ["外滩核心位置", "历史建筑底蕴", "江景房视野极佳", "适合追求品质和体验"],
            "cons": ["价格较高"],
        },
        # ── 中端舒适型 ──
        {
            "name": "上海锦江饭店",
            "area": "淮海路-茂名南路区域",
            "price_per_night": 600,
            "type": "舒适型酒店",
            "nearest_subway": "陕西南路",
            "tags": ["舒适", "历史经典", "淮海路商圈", "性价比"],
            "pros": ["淮海路核心区位", "法式建筑风格", "房间宽敞", "性价比高"],
            "cons": ["设施略有年代感"],
        },
        {
            "name": "上海虹桥宾馆",
            "area": "虹桥-古北区域",
            "price_per_night": 400,
            "type": "舒适型酒店",
            "nearest_subway": "虹桥路/伊犁路",
            "tags": ["舒适", "近虹桥枢纽", "性价比", "交通方便"],
            "pros": ["靠近虹桥机场和火车站", "地铁通达", "价格适中", "适合商务出行"],
            "cons": ["距离外滩等核心景区需地铁约30分钟"],
        },
        # ── 经济型 ──
        {
            "name": "人民广场经济酒店",
            "area": "人民广场-南京路区域",
            "price_per_night": 260,
            "type": "经济型酒店",
            "nearest_subway": "人民广场",
            "tags": ["地铁方便", "市中心", "低预算"],
            "pros": ["地铁换乘方便", "靠近南京路"],
            "cons": ["热门区域价格波动大"],
        },
        {
            "name": "上海静安便捷酒店",
            "area": "静安寺区域",
            "price_per_night": 260,
            "type": "经济型酒店",
            "nearest_subway": "静安寺",
            "tags": ["低预算", "地铁方便", "市中心", "公共交通方便"],
            "pros": ["静安寺商圈", "地铁2号线方便", "价格实惠"],
            "cons": ["房间面积偏小"],
        },
    ],
    "广州": [
        # ── 奢华/高端 ──
        {
            "name": "广州四季酒店",
            "area": "珠江新城区域",
            "price_per_night": 1800,
            "type": "奢华五星酒店",
            "nearest_subway": "珠江新城",
            "tags": ["奢华", "珠江新城核心", "天际线景观", "高品质"],
            "pros": ["珠江新城CBD核心", "广州塔景观", "设施顶级", "服务一流"],
            "cons": ["价格较高"],
        },
        {
            "name": "广州文华东方酒店",
            "area": "天河-体育中心区域",
            "price_per_night": 2000,
            "type": "奢华五星酒店",
            "nearest_subway": "石牌桥",
            "tags": ["奢华", "太古汇核心", "设计师酒店", "高端商务"],
            "pros": ["太古汇商圈核心", "设计感极强", "餐饮顶级", "适合商务和度假"],
            "cons": ["价格较高"],
        },
        # ── 中端舒适型 ──
        {
            "name": "广州花园酒店",
            "area": "环市东路区域",
            "price_per_night": 500,
            "type": "舒适型酒店",
            "nearest_subway": "淘金/区庄",
            "tags": ["舒适", "经典地标", "花园式", "性价比"],
            "pros": ["广州经典地标酒店", "花园式环境", "房间宽敞", "性价比高"],
            "cons": ["设施略有年代感"],
        },
        {
            "name": "广州中国大酒店",
            "area": "越秀-流花湖区域",
            "price_per_night": 450,
            "type": "舒适型酒店",
            "nearest_subway": "越秀公园/广州火车站",
            "tags": ["舒适", "近越秀公园", "交通枢纽", "性价比"],
            "pros": ["靠近越秀公园", "广州火车站交通便利", "价格适中", "配套齐全"],
            "cons": ["距离珠江新城核心区需地铁约15分钟"],
        },
        # ── 经济型 ──
        {
            "name": "广州荔湾便捷酒店",
            "area": "荔湾-上下九区域",
            "price_per_night": 230,
            "type": "经济型酒店",
            "nearest_subway": "长寿路",
            "tags": ["低预算", "地铁方便", "近上下九", "老城核心"],
            "pros": ["靠近上下九步行街", "地铁1号线方便", "美食丰富", "价格实惠"],
            "cons": ["老城区房间面积偏小"],
        },
    ],
    "杭州": [
        {
            "name": "湖滨西湖便捷酒店",
            "area": "湖滨-西湖边",
            "price_per_night": 280,
            "type": "经济型酒店",
            "nearest_subway": "龙翔桥",
            "tags": ["地铁方便", "近西湖", "公共交通方便", "市中心"],
            "pros": ["步行可到西湖湖滨", "地铁和公交换乘方便", "适合首次游览杭州"],
            "cons": ["热门区域节假日价格可能上涨"],
        },
        {
            "name": "武林广场地铁酒店",
            "area": "武林广场",
            "price_per_night": 240,
            "type": "经济型酒店",
            "nearest_subway": "武林广场",
            "tags": ["低预算", "地铁方便", "公共交通方便", "市中心"],
            "pros": ["多条线路换乘方便", "前往西湖和运河都较顺路", "价格相对稳妥"],
            "cons": ["距离湖滨核心景观需短途地铁或公交"],
        },
        {
            "name": "杭州东站轻住酒店",
            "area": "杭州东站附近",
            "price_per_night": 220,
            "type": "经济型酒店",
            "nearest_subway": "火车东站",
            "tags": ["低预算", "高铁方便", "地铁方便"],
            "pros": ["适合高铁往返", "地铁接入主城区方便", "价格较低"],
            "cons": ["去西湖和灵隐寺通勤时间稍长"],
        },
    ],
    "南京": [
        {
            "name": "新街口地铁精选酒店",
            "area": "新街口",
            "price_per_night": 260,
            "type": "经济型酒店",
            "nearest_subway": "新街口",
            "tags": ["地铁方便", "市中心", "公共交通方便"],
            "pros": ["地铁换乘便利", "前往总统府、夫子庙和钟山都较均衡", "餐饮选择多"],
            "cons": ["核心商圈价格可能波动"],
        },
        {
            "name": "夫子庙秦淮客栈",
            "area": "夫子庙-秦淮河区域",
            "price_per_night": 230,
            "type": "经济型客栈",
            "nearest_subway": "夫子庙",
            "tags": ["低预算", "近夫子庙", "地铁方便", "公共交通方便"],
            "pros": ["夜游秦淮河方便", "步行覆盖夫子庙周边", "适合低预算"],
            "cons": ["热门街区夜间可能较热闹"],
        },
        {
            "name": "南京南站便捷酒店",
            "area": "南京南站附近",
            "price_per_night": 210,
            "type": "经济型酒店",
            "nearest_subway": "南京南站",
            "tags": ["低预算", "高铁方便", "地铁方便"],
            "pros": ["高铁抵离方便", "价格相对低", "地铁进城线路明确"],
            "cons": ["距离主要景点通勤时间略长"],
        },
    ],
    "深圳": [
        # ── 奢华/高端 ──
        {
            "name": "深圳瑞吉酒店",
            "area": "罗湖区",
            "price_per_night": 1200,
            "type": "豪华五星酒店",
            "nearest_subway": "大剧院",
            "tags": ["奢华", "天际线景观", "罗湖核心"],
            "pros": ["京基100大厦顶层，俯瞰深圳", "设施顶级", "服务一流"],
            "cons": ["价格较高"],
        },
        {
            "name": "深圳华侨城洲际大酒店",
            "area": "南山区",
            "price_per_night": 1500,
            "type": "奢华五星酒店",
            "nearest_subway": "世界之窗/华侨城",
            "tags": ["奢华", "度假", "近华侨城景区"],
            "pros": ["紧邻世界之窗和欢乐谷", "热带园林风格", "亲子友好"],
            "cons": ["价格较高"],
        },
        # ── 中端舒适型 ──
        {
            "name": "深圳福田香格里拉",
            "area": "福田区",
            "price_per_night": 600,
            "type": "豪华四星酒店",
            "nearest_subway": "会展中心",
            "tags": ["舒适", "商务", "CBD核心"],
            "pros": ["福田CBD位置", "交通便捷", "性价比高"],
            "cons": ["价格适中偏高"],
        },
        {
            "name": "深圳南山智选假日酒店",
            "area": "南山区",
            "price_per_night": 380,
            "type": "舒适型酒店",
            "nearest_subway": "南山书城",
            "tags": ["舒适", "国际品牌", "性价比"],
            "pros": ["南山科技园附近", "国际品牌标准", "早餐丰盛"],
            "cons": ["距离传统景点需地铁换乘"],
        },
        # ── 经济型 ──
        {
            "name": "深圳罗湖口岸便捷酒店",
            "area": "罗湖区",
            "price_per_night": 200,
            "type": "经济型酒店",
            "nearest_subway": "罗湖",
            "tags": ["低预算", "口岸方便", "地铁方便"],
            "pros": ["靠近罗湖口岸", "地铁1号线沿线", "价格实惠"],
            "cons": ["房间面积较小"],
        },
    ],
    "成都": [
        # ── 奢华/高端 ──
        {
            "name": "成都博舍酒店",
            "area": "锦江区",
            "price_per_night": 1800,
            "type": "奢华精品酒店",
            "nearest_subway": "春熙路",
            "tags": ["奢华", "设计师酒店", "太古里核心"],
            "pros": ["毗邻太古里和IFS", "设计感极强", "餐饮顶级"],
            "cons": ["价格较高"],
        },
        {
            "name": "成都瑞吉酒店",
            "area": "锦江区",
            "price_per_night": 1300,
            "type": "豪华五星酒店",
            "nearest_subway": "天府广场",
            "tags": ["奢华", "商务", "高品质"],
            "pros": ["天府广场核心区位", "房间宽敞", "管家服务"],
            "cons": ["价格较高"],
        },
        # ── 中端舒适型 ──
        {
            "name": "成都锦江宾馆",
            "area": "锦江区",
            "price_per_night": 450,
            "type": "舒适型酒店",
            "nearest_subway": "锦江宾馆",
            "tags": ["舒适", "历史经典", "近春熙路"],
            "pros": ["历史悠久口碑好", "锦江畔位置好", "性价比高"],
            "cons": ["设施略有年代感"],
        },
        {
            "name": "成都宽窄巷子亚朵酒店",
            "area": "青羊区",
            "price_per_night": 500,
            "type": "舒适型酒店",
            "nearest_subway": "宽窄巷子",
            "tags": ["舒适", "近宽窄巷子", "文化氛围"],
            "pros": ["步行可达宽窄巷子", "设计有成都特色", "服务贴心"],
            "cons": ["节假日价格较高"],
        },
        # ── 经济型 ──
        {
            "name": "成都武侯祠如家酒店",
            "area": "武侯区",
            "price_per_night": 180,
            "type": "经济型酒店",
            "nearest_subway": "高升桥",
            "tags": ["低预算", "近武侯祠", "地铁方便"],
            "pros": ["靠近武侯祠和锦里", "地铁3号线方便", "价格实惠"],
            "cons": ["设施基础"],
        },
    ],
    "重庆": [
        # ── 奢华/高端 ──
        {
            "name": "重庆来福士洲际酒店",
            "area": "渝中区",
            "price_per_night": 1600,
            "type": "奢华五星酒店",
            "nearest_subway": "小什字",
            "tags": ["奢华", "地标建筑", "江景"],
            "pros": ["来福士地标", "两江交汇景观", "设施顶级"],
            "cons": ["价格较高"],
        },
        {
            "name": "重庆解放碑威斯汀酒店",
            "area": "渝中区",
            "price_per_night": 1200,
            "type": "豪华五星酒店",
            "nearest_subway": "较场口",
            "tags": ["奢华", "解放碑核心", "高空景观"],
            "pros": ["解放碑核心位置", "高空江景房", "餐饮出色"],
            "cons": ["价格较高"],
        },
        # ── 中端舒适型 ──
        {
            "name": "重庆洪崖洞逸居酒店",
            "area": "渝中区",
            "price_per_night": 400,
            "type": "舒适型酒店",
            "nearest_subway": "小什字",
            "tags": ["舒适", "近洪崖洞", "夜景"],
            "pros": ["紧邻洪崖洞", "可观江景", "性价比高"],
            "cons": ["噪音可能稍大"],
        },
        {
            "name": "重庆观音桥全季酒店",
            "area": "江北区",
            "price_per_night": 350,
            "type": "舒适型酒店",
            "nearest_subway": "观音桥",
            "tags": ["舒适", "商圈核心", "性价比"],
            "pros": ["观音桥商圈", "地铁3号线便利", "周边餐饮丰富"],
            "cons": ["距离渝中区景点需过江"],
        },
        # ── 经济型 ──
        {
            "name": "重庆沙坪坝汉庭酒店",
            "area": "沙坪坝区",
            "price_per_night": 170,
            "type": "经济型酒店",
            "nearest_subway": "沙坪坝",
            "tags": ["低预算", "近磁器口", "地铁方便"],
            "pros": ["靠近磁器口古镇", "交通便利", "价格低"],
            "cons": ["距离渝中区核心景点较远"],
        },
    ],
    "武汉": [
        # ── 奢华/高端 ──
        {
            "name": "武汉万达瑞华酒店",
            "area": "武昌区",
            "price_per_night": 1400,
            "type": "奢华五星酒店",
            "nearest_subway": "楚河汉街",
            "tags": ["奢华", "东湖", "商务"],
            "pros": ["紧邻东湖和汉街", "设施奢华", "湖景房"],
            "cons": ["价格较高"],
        },
        {
            "name": "武汉汉口江滩酒店",
            "area": "江岸区",
            "price_per_night": 800,
            "type": "豪华五星酒店",
            "nearest_subway": "江汉路",
            "tags": ["豪华", "江滩景观", "历史建筑"],
            "pros": ["江滩一线景观", "汉口历史风貌", "出行方便"],
            "cons": ["价格偏高"],
        },
        # ── 中端舒适型 ──
        {
            "name": "武汉黄鹤楼亚朵酒店",
            "area": "武昌区",
            "price_per_night": 450,
            "type": "舒适型酒店",
            "nearest_subway": "司门口黄鹤楼",
            "tags": ["舒适", "近黄鹤楼", "地铁方便"],
            "pros": ["步行至黄鹤楼", "地铁5号线直达", "性价比高"],
            "cons": ["节假日价格可能上涨"],
        },
        {
            "name": "武汉楚河汉街全季酒店",
            "area": "武昌区",
            "price_per_night": 380,
            "type": "舒适型酒店",
            "nearest_subway": "楚河汉街",
            "tags": ["舒适", "汉街商圈", "近东湖"],
            "pros": ["汉街商圈核心", "近东湖和省博", "品牌连锁"],
            "cons": ["旺季需提前预订"],
        },
        # ── 经济型 ──
        {
            "name": "武汉户部巷便捷酒店",
            "area": "武昌区",
            "price_per_night": 180,
            "type": "经济型酒店",
            "nearest_subway": "司门口黄鹤楼",
            "tags": ["低预算", "近户部巷", "美食方便"],
            "pros": ["户部巷和江边步行可达", "价格低", "餐饮选择多"],
            "cons": ["设施简单", "可能较吵"],
        },
    ],
    "西安": [
        # ── 奢华/高端 ──
        {
            "name": "西安索菲特传奇酒店",
            "area": "新城区",
            "price_per_night": 1600,
            "type": "奢华五星酒店",
            "nearest_subway": "北大街",
            "tags": ["奢华", "历史建筑", "市中心"],
            "pros": ["人民大厦历史建筑", "市中心核心区位", "顶级服务"],
            "cons": ["价格较高"],
        },
        {
            "name": "西安威斯汀大酒店",
            "area": "雁塔区",
            "price_per_night": 1000,
            "type": "豪华五星酒店",
            "nearest_subway": "大雁塔",
            "tags": ["豪华", "近大雁塔", "博物馆式"],
            "pros": ["紧邻大雁塔", "自带博物馆", "大唐不夜城步行可达"],
            "cons": ["价格偏高"],
        },
        # ── 中端舒适型 ──
        {
            "name": "西安钟楼如家精选酒店",
            "area": "莲湖区",
            "price_per_night": 350,
            "type": "舒适型酒店",
            "nearest_subway": "钟楼",
            "tags": ["舒适", "近钟楼", "地铁方便"],
            "pros": ["钟楼核心位置", "地铁2号线直达", "回民街步行可达"],
            "cons": ["核心区域停车不便"],
        },
        {
            "name": "西安大唐不夜城亚朵酒店",
            "area": "雁塔区",
            "price_per_night": 480,
            "type": "舒适型酒店",
            "nearest_subway": "大雁塔",
            "tags": ["舒适", "不夜城", "近大雁塔"],
            "pros": ["大唐不夜城步行可达", "大雁塔景观", "文化氛围"],
            "cons": ["夜间略热闹"],
        },
        # ── 经济型 ──
        {
            "name": "西安火车站汉庭酒店",
            "area": "新城区",
            "price_per_night": 180,
            "type": "经济型酒店",
            "nearest_subway": "西安站/五路口",
            "tags": ["低预算", "高铁方便", "地铁方便"],
            "pros": ["火车站旁方便出行", "去兵马俑交通便利", "价格实惠"],
            "cons": ["距离钟楼核心区略远"],
        },
    ],
    "苏州": [
        # ── 奢华/高端 ──
        {
            "name": "苏州柏悦酒店",
            "area": "吴中区/工业园区",
            "price_per_night": 2000,
            "type": "奢华五星酒店",
            "nearest_subway": "时代广场",
            "tags": ["奢华", "金鸡湖畔", "现代设计"],
            "pros": ["金鸡湖畔景观", "设计精致", "设施顶级"],
            "cons": ["价格较高", "距离古城区较远"],
        },
        {
            "name": "苏州南园宾馆",
            "area": "姑苏区",
            "price_per_night": 800,
            "type": "豪华园林酒店",
            "nearest_subway": "三元坊",
            "tags": ["豪华", "园林风格", "姑苏核心"],
            "pros": ["苏州园林式酒店", "古城区核心", "环境幽雅"],
            "cons": ["设施偏传统"],
        },
        # ── 中端舒适型 ──
        {
            "name": "苏州拙政园和颐酒店",
            "area": "姑苏区",
            "price_per_night": 500,
            "type": "舒适型酒店",
            "nearest_subway": "北寺塔",
            "tags": ["舒适", "近拙政园", "古城区"],
            "pros": ["步行可达拙政园和苏博", "古城区核心", "交通方便"],
            "cons": ["古城区停车不便"],
        },
        {
            "name": "苏州山塘街民宿酒店",
            "area": "姑苏区",
            "price_per_night": 350,
            "type": "舒适型民宿",
            "nearest_subway": "山塘街",
            "tags": ["舒适", "近山塘街", "江南风情"],
            "pros": ["山塘街水乡体验", "苏州特色装修", "性价比高"],
            "cons": ["隔音可能一般"],
        },
        # ── 经济型 ──
        {
            "name": "苏州火车站7天酒店",
            "area": "姑苏区",
            "price_per_night": 170,
            "type": "经济型酒店",
            "nearest_subway": "苏州火车站",
            "tags": ["低预算", "高铁方便", "地铁方便"],
            "pros": ["火车站旁", "价格低", "地铁覆盖主要景点"],
            "cons": ["房间面积小"],
        },
    ],
    "天津": [
        # ── 奢华/高端 ──
        {
            "name": "天津丽思卡尔顿酒店",
            "area": "和平区",
            "price_per_night": 1500,
            "type": "奢华五星酒店",
            "nearest_subway": "小白楼",
            "tags": ["奢华", "英式建筑", "五大道旁"],
            "pros": ["历史建筑改建", "五大道核心区位", "英式管家服务"],
            "cons": ["价格较高"],
        },
        {
            "name": "天津海河悦榕庄",
            "area": "河北区",
            "price_per_night": 1100,
            "type": "豪华五星酒店",
            "nearest_subway": "天津站",
            "tags": ["豪华", "海河景观", "静谧"],
            "pros": ["海河一线景观", "靠近天津站", "SPA出色"],
            "cons": ["价格偏高"],
        },
        # ── 中端舒适型 ──
        {
            "name": "天津滨江道全季酒店",
            "area": "和平区",
            "price_per_night": 400,
            "type": "舒适型酒店",
            "nearest_subway": "营口道",
            "tags": ["舒适", "滨江道", "市中心"],
            "pros": ["滨江道商圈核心", "近瓷房子和五大道", "品牌连锁"],
            "cons": ["旺季价格浮动"],
        },
        {
            "name": "天津之眼锦江之星酒店",
            "area": "河北区",
            "price_per_night": 300,
            "type": "舒适型酒店",
            "nearest_subway": "天津之眼周边",
            "tags": ["舒适", "近天津之眼", "海河沿岸"],
            "pros": ["天津之眼步行可达", "海河风光", "性价比高"],
            "cons": ["距离五大道略远"],
        },
        # ── 经济型 ──
        {
            "name": "天津古文化街如家酒店",
            "area": "南开区",
            "price_per_night": 180,
            "type": "经济型酒店",
            "nearest_subway": "东南角",
            "tags": ["低预算", "近古文化街", "地铁方便"],
            "pros": ["靠近古文化街", "价格低", "地铁直达"],
            "cons": ["设施基础"],
        },
    ],
}


def search_hotels(
    city: str = DEFAULT_CITY,
    preferred_areas: list[str] | None = None,
    target_area: str | None = None,
    budget_level: str = "normal",
    days: int = 3,
    daily_plan: dict[str, Any] | None = None,
    preferences: list[str] | None = None,
    area_selection: dict[str, Any] | None = None,
    requested_fields: list[str] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Return mock hotels for the selected area, then rank by budget and transport.

    Hotel Agent v11 first asks the LLM to choose a target accommodation area,
    then calls this MCP method to retrieve hotels in that area. If the exact
    target area has no candidates in the mock dataset, we fall back to globally
    ranked candidates but mark area_filter_fallback=True.
    """
    normalized_city = (city or DEFAULT_CITY).strip()
    all_hotels: list[dict[str, Any]] = deepcopy(HOTEL_DATA.get(normalized_city) or [])
    preferred_areas = preferred_areas or []
    preferences = preferences or []
    requested_fields = requested_fields or []
    target_area = (target_area or (preferred_areas[0] if preferred_areas else "") or "").strip()

    plan_areas: list[str] = []
    if isinstance(daily_plan, dict):
        for item in daily_plan.values():
            if isinstance(item, dict) and item.get("area"):
                plan_areas.append(str(item.get("area")))
    all_area_hints = ([target_area] if target_area else []) + preferred_areas + plan_areas

    def area_matches(hotel: dict[str, Any], area_hint: str) -> bool:
        # 判断酒店是否匹配目标区域提示
        area = str(hotel.get("area", ""))
        hint = str(area_hint or "")
        if not area or not hint:
            return False
        return area == hint or area in hint or hint in area

    if target_area:
        filtered_hotels = [hotel for hotel in all_hotels if area_matches(hotel, target_area)]
    else:
        filtered_hotels = []

    area_filter_fallback = False
    hotels = filtered_hotels
    if not hotels:
        area_filter_fallback = bool(target_area)
        hotels = all_hotels

    def score(hotel: dict[str, Any]) -> int:
        # 根据区域匹配度、预算和偏好给酒店排序打分
        value = 0
        area = str(hotel.get("area", ""))
        tags = [str(x) for x in hotel.get("tags", [])]
        price = int(hotel.get("price_per_night") or 9999)

        if target_area and area_matches(hotel, target_area):
            value += 100

        for hint in all_area_hints:
            hint = str(hint)
            if not hint:
                continue
            if hint == area or area in hint or hint in area:
                value += 35
            elif any(part and part in hint for part in area.split("-")[:1]):
                value += 12

        if budget_level == "low":
            value += max(0, 80 - price // 5)
            if "低预算" in tags or "性价比" in tags:
                value += 25

        if "公共交通方便" in preferences or "地铁方便" in preferences:
            if "地铁方便" in tags or "公共交通方便" in tags or "地铁枢纽" in tags:
                value += 20

        if "近天安门" in tags or "近故宫" in tags:
            value += 10
        return value

    hotels.sort(key=score, reverse=True)

    if requested_fields:
        keep = set(requested_fields) | {"name", "area", "price_per_night", "nearest_subway", "tags"}
        hotels = [{key: value for key, value in hotel.items() if key in keep} for hotel in hotels]

    return {
        "city": normalized_city,
        "requested_city": normalized_city,
        "fallback_used": normalized_city not in HOTEL_DATA,
        "days": days,
        "budget_level": budget_level,
        "target_area": target_area,
        "preferred_areas": preferred_areas,
        "area_selection": area_selection or {},
        "area_filter_fallback": area_filter_fallback,
        "hotels": hotels,
    }
