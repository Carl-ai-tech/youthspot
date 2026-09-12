"""新北市 29 個行政區的界線 → SVG 路徑。

為什麼要自己解 TopoJSON：**瀏覽器端要保持零第三方套件。**
如果把 topojson-client 丟進網頁，就得為了一張地圖賠掉整個專案的架構主張。
改成在這一層（build 時、Python 標準函式庫）解完、投影完、簡化完，
只把 `<path d="...">` 的字串交給前端 —— 網頁端一行函式庫都不用載。

資料來源：taiwan-atlas（內政部國土測繪中心的鄉鎮市區界線，已量化簡化）
    https://www.npmjs.com/package/taiwan-atlas
界線幾年才變一次，所以鎖版本 2021.9.20，不追最新。

三步：
    1. 解 TopoJSON  量化的 delta 編碼 → 經緯度環
    2. 投影         等距長方投影，緯度乘 cos(中心緯度) 修正
    3. 簡化         Douglas-Peucker，把 500KB 的全臺灣壓成幾十 KB 的新北市

投影用等距長方而不是麥卡托：一個縣市跨不到 0.7 個緯度，
兩者差異在畫面上看不出來，但等距長方少一個 log/tan，錯的機會也少一個。
"""

from __future__ import annotations

import json
import math
import urllib.request
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "_cache"
SOURCE = "https://cdn.jsdelivr.net/npm/taiwan-atlas@2021.9.20/towns-10t.json"
DATASET = "內政部國土測繪中心 鄉鎮市區界線（經 taiwan-atlas 量化）"
LANDING = "https://www.npmjs.com/package/taiwan-atlas"

VIEWBOX = 1000.0          # SVG 座標系邊長，前端用 viewBox 縮放
SIMPLIFY_TOLERANCE = 0.45  # 以 VIEWBOX 為單位；再大就會看到鋸齒
EXPECTED_DISTRICTS = 29    # 新北市的行政區數，對不上就是資料換版了
# 六都各自的行政區數；atlas 的縣市名寫「台」，我們的資料寫「臺」
DISTRICTS_BY_CITY = {"新北市": 29, "臺北市": 12, "桃園市": 13, "臺中市": 29, "臺南市": 37, "高雄市": 38}


def _download(refresh: bool = False) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "towns-10t.json"
    if refresh or not path.exists():
        req = urllib.request.Request(SOURCE, headers={"User-Agent": "YouthLens/1.0"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            path.write_bytes(resp.read())
    return json.loads(path.read_text(encoding="utf-8"))


def _decode_arcs(topo: dict) -> list[list[tuple[float, float]]]:
    """量化的 delta 編碼 → 實際經緯度。

    TopoJSON 為了縮小體積做兩件事：座標存成整數格點、每個點只存跟前一點的差。
    所以要先累加還原、再乘回 scale/translate。
    """
    scale = topo["transform"]["scale"]
    translate = topo["transform"]["translate"]
    out = []
    for arc in topo["arcs"]:
        x = y = 0
        ring = []
        for dx, dy in arc:
            x += dx
            y += dy
            ring.append((x * scale[0] + translate[0], y * scale[1] + translate[1]))
        out.append(ring)
    return out


def _ring_points(indices, arcs) -> list[tuple[float, float]]:
    """把一圈的 arc 索引串成連續的點。

    索引為負數代表「這條 arc 要反著走」，換算方式是 ~i（等於 -i-1）。
    接起來時要去掉重複的接點，否則簡化演算法會被零長度線段干擾。
    """
    pts: list[tuple[float, float]] = []
    for idx in indices:
        arc = arcs[~idx][::-1] if idx < 0 else arcs[idx]
        pts.extend(arc[1:] if pts else arc)
    return pts


def _perp_distance(p, a, b) -> float:
    """點到線段的垂直距離。Douglas-Peucker 的內圈，寫成純算式避免建物件。"""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _simplify(points: list, tol: float) -> list:
    """Douglas-Peucker。用迴圈不用遞迴 —— 海岸線的點很多，遞迴會爆堆疊。"""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        worst, worst_i = tol, -1
        for i in range(lo + 1, hi):
            d = _perp_distance(points[i], points[lo], points[hi])
            if d > worst:
                worst, worst_i = d, i
        if worst_i >= 0:
            keep[worst_i] = True
            stack.append((lo, worst_i))
            stack.append((worst_i, hi))
    return [p for p, k in zip(points, keep) if k]


def fetch_district_shapes(region: str = "新北市", *, refresh: bool = False) -> dict:
    """回傳 {"viewBox": ..., "districts": {區名: {"d": SVG路徑, "cx":…, "cy":…}}}。

    `cx`/`cy` 是標籤要放的位置，用面積加權重心而不是外接矩形中心 ——
    像淡水、瑞芳這種細長或彎曲的區，矩形中心會落在區外。
    """
    topo = _download(refresh)
    arcs = _decode_arcs(topo)
    expected = DISTRICTS_BY_CITY.get(region, EXPECTED_DISTRICTS)
    geoms = [g for g in topo["objects"]["towns"]["geometries"]
             if (g.get("properties", {}).get("COUNTYNAME") or "").replace("台", "臺") == region]
    if len(geoms) != expected:
        raise RuntimeError(
            f"{region} 應該有 {expected} 個行政區，實際取到 {len(geoms)} 個 —— "
            "來源資料可能換版了，先確認 taiwan-atlas 的版本再往下跑"
        )

    # 先全部解成經緯度環，才知道整體範圍要怎麼投影
    raw: dict[str, list[list[tuple[float, float]]]] = {}
    for g in geoms:
        name = g["properties"]["TOWNNAME"]
        polys = [g["arcs"]] if g["type"] == "Polygon" else g["arcs"]
        raw[name] = [_ring_points(ring, arcs) for poly in polys for ring in poly]

    all_pts = [p for rings in raw.values() for r in rings for p in r]
    lons = [p[0] for p in all_pts]
    lats = [p[1] for p in all_pts]
    lon0, lon1 = min(lons), max(lons)
    lat0, lat1 = min(lats), max(lats)
    # 等距長方：經度方向要乘 cos(緯度) 才不會把圖壓扁
    kx = math.cos(math.radians((lat0 + lat1) / 2))
    w = (lon1 - lon0) * kx
    h = lat1 - lat0
    scale = VIEWBOX / max(w, h)
    # 置中：短的那一邊補一半的差
    off_x = (VIEWBOX - w * scale) / 2
    off_y = (VIEWBOX - h * scale) / 2

    def project(pt):
        x = (pt[0] - lon0) * kx * scale + off_x
        y = (lat1 - pt[1]) * scale + off_y      # 緯度往上，SVG 的 y 往下
        return (x, y)

    districts = {}
    for name, rings in raw.items():
        parts, area_sum, cx_sum, cy_sum = [], 0.0, 0.0, 0.0
        for ring in rings:
            pts = _simplify([project(p) for p in ring], SIMPLIFY_TOLERANCE)
            if len(pts) < 3:
                continue
            parts.append("M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z")
            # 多邊形面積與重心（鞋帶公式）
            a = cx = cy = 0.0
            for i in range(len(pts)):
                x0, y0 = pts[i]
                x1, y1 = pts[(i + 1) % len(pts)]
                cross = x0 * y1 - x1 * y0
                a += cross
                cx += (x0 + x1) * cross
                cy += (y0 + y1) * cross
            if a:
                area_sum += abs(a / 2)
                cx_sum += abs(a / 2) * (cx / (3 * a))
                cy_sum += abs(a / 2) * (cy / (3 * a))
        if not parts:
            continue
        districts[name] = {
            "d": "".join(parts),
            "cx": round(cx_sum / area_sum, 1) if area_sum else 0,
            "cy": round(cy_sum / area_sum, 1) if area_sum else 0,
        }

    return {
        "viewBox": f"0 0 {VIEWBOX:.0f} {VIEWBOX:.0f}",
        "source": DATASET,
        "url": LANDING,
        "districts": districts,
    }


if __name__ == "__main__":
    shapes = fetch_district_shapes()
    blob = json.dumps(shapes, ensure_ascii=False)
    print(f"{len(shapes['districts'])} 個行政區　SVG 資料 {len(blob) / 1024:.1f} KB")
    for name, d in sorted(shapes["districts"].items(),
                          key=lambda kv: -len(kv[1]["d"]))[:5]:
        print(f"  {name:6s} 路徑 {len(d['d']):6,} 字元　標籤位置 ({d['cx']}, {d['cy']})")
