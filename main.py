from fastapi import FastAPI, HTTPException

import osmnx as ox
import networkx as nx
import geopandas as gpd
import pandas as pd

from geopy.geocoders import Nominatim
from pyproj import Transformer

import json

app = FastAPI()

@app.get("/")
def root():
    return {
        "message": "Safe Route API is running"
    }

##################################################
# 道路ネットワーク読込
##################################################

print("Loading road network...")

G = ox.load_graphml("graph.graphml")

print("GRAPH CRS:", G.graph.get("crs"), flush=True)

##################################################
# インシデントデータ読込
##################################################

files = {
    "snatch_theft":
        "data/snatch_theft.geojson",

    "motorcycle_theft":
        "data/motorcycle_theft.geojson",

    "bicycle_theft":
        "data/bicycle_theft.geojson",

    "vendingmachine_theft":
        "data/vendingmachine_theft.geojson",

    "traffic_accident":
        "data/traffic_accident.geojson"
}

frames = []

for incident_type, path in files.items():

    print(f"Loading {incident_type}: {path}")

    gdf = gpd.read_file(path)

    if gdf.crs is None:
        gdf.set_crs("EPSG:4326", inplace=True)

    gdf["incident_type"] = incident_type

    frames.append(gdf)

incident_gdf = gpd.GeoDataFrame(
    pd.concat(frames, ignore_index=True)
)

incident_gdf = incident_gdf.to_crs(
    epsg=3857
)

##################################################
# ジオコーダ
##################################################

geocoder = Nominatim(
    user_agent="tsuzuki_route_app"
)

##################################################
# ジオコーディング
##################################################

def geocode(place_name):

    location = geocoder.geocode(
        place_name,
        country_codes="jp"
    )

    if location is None:

        raise HTTPException(
            status_code=404,
            detail=f"{place_name} not found"
        )

    return (
        location.latitude,
        location.longitude
    )

##################################################
# ペナルティ設定
##################################################

PENALTY = {

    "snatch_theft": 3000,

    "motorcycle_theft": 1000,

    "bicycle_theft": 700,

    "vendingmachine_theft": 300,

    "traffic_accident": 1500
}

##################################################
# リスク付きグラフ作成
##################################################

def create_risk_graph(
    graph,
    buffer_distance
):

    print(
        f"create_risk_graph buffer={buffer_distance}",
        flush=True
    )

    H = graph.copy()

    print(
        "graph copied",
        flush=True
    )

    edges = ox.graph_to_gdfs(
        H,
        nodes=False,
        edges=True
    )

    print(
        f"graph_to_gdfs done, edges={len(edges)}",
        flush=True
    )

    edges = edges.to_crs(
        epsg=3857
    )

    print(
        "edges to_crs done",
        flush=True
    )

    for idx, edge in edges.iterrows():

        risk_cost = edge["length"]

        nearby = incident_gdf[
            incident_gdf.distance(
                edge.geometry
            ) <= buffer_distance
        ]

        for _, incident in nearby.iterrows():

            risk_cost += PENALTY[
                incident["incident_type"]
            ]

        u, v, k = idx

        H[u][v][k]["risk_cost"] = risk_cost

    print(
        "risk graph completed",
        flush=True
    )

    return H

##################################################
# 出発ノード・到着ノード取得
##################################################

def get_nodes(
    graph,
    start_lat,
    start_lon,
    goal_lat,
    goal_lon
):

    transformer = Transformer.from_crs(
        "EPSG:4326",
        graph.graph["crs"],
        always_xy=True
    )

    start_x, start_y = transformer.transform(
        start_lon,
        start_lat
    )

    goal_x, goal_y = transformer.transform(
        goal_lon,
        goal_lat
    )

    print(
        f"start projected: {start_x}, {start_y}",
        flush=True
    )

    print(
        f"goal projected: {goal_x}, {goal_y}",
        flush=True
    )

    start_node = ox.distance.nearest_nodes(
        graph,
        start_x,
        start_y
    )

    goal_node = ox.distance.nearest_nodes(
        graph,
        goal_x,
        goal_y
    )

    return start_node, goal_node

##################################################
# ルート探索
##################################################

def calculate_route(
    graph,
    start_node,
    goal_node,
    weight
):

    return nx.shortest_path(
        graph,
        start_node,
        goal_node,
        weight=weight
    )

##################################################
# 距離計算
##################################################

def calculate_distance(
    graph,
    route
):

    distance = 0

    for u, v in zip(
        route[:-1],
        route[1:]
    ):

        edge_length = min(
            d["length"]
            for d in graph[u][v].values()
        )

        distance += edge_length

    return round(distance, 1)

##################################################
# Route → GeoDataFrame
##################################################

def route_to_gdf(
    graph,
    route
):

    return ox.routing.route_to_gdf(
        graph,
        route
    )

##################################################
# インシデント集計
##################################################

def calculate_incidents(
    route_gdf,
    search_distance=50
):

    route_gdf = route_gdf.to_crs(
        epsg=3857
    )

    route_buffer = (
        route_gdf.unary_union.buffer(
            search_distance
        )
    )

    nearby = incident_gdf[
        incident_gdf.intersects(
            route_buffer
        )
    ]

    breakdown = (
        nearby["incident_type"]
        .value_counts()
        .to_dict()
    )

    return len(nearby), breakdown

##################################################
# GeoJSON変換
##################################################

def to_geojson(
    route_gdf
):

    return json.loads(
        route_gdf.to_json()
    )

##################################################
# 結果生成
##################################################

def build_result(
    graph,
    route
):

    print(
        f"build_result route length={len(route)}",
        flush=True
    )

    rgdf = route_to_gdf(
        graph,
        route
    )

    incident_count, breakdown = (
        calculate_incidents(
            rgdf
        )
    )

    return {

        "distance_m":
            calculate_distance(
                graph,
                route
            ),

        "incident_count":
            incident_count,

        "incident_breakdown":
            breakdown,

        "geojson":
            to_geojson(
                rgdf
            )
    }

##################################################
# API
##################################################
from fastapi import FastAPI, HTTPException, Query

@app.get("/route")
def get_route(
    origin: str,
    destination: str
):

    print(f"origin={origin}", flush=True)
    print(f"destination={destination}", flush=True)

    start_lat, start_lon = geocode(origin)

    print(f"start={start_lat},{start_lon}", flush=True)

    goal_lat, goal_lon = geocode(destination)

    print(f"goal={goal_lat},{goal_lon}", flush=True)

    start_node, goal_node = get_nodes(
        G,
        start_lat,
        start_lon,
        goal_lat,
        goal_lon
    )

    print(
        f"start_node={start_node}, goal_node={goal_node}",
        flush=True
    )

    print("calculating shortest route", flush=True)

    ################################################
    # 最短ルート
    ################################################

    shortest_route = calculate_route(
        G,
        start_node,
        goal_node,
        "length"
    )

    print("shortest route done", flush=True)

    ################################################
    # 回避ルート
    ################################################
    
    print("creating medium graph", flush=True)

    medium_graph = create_risk_graph(
        G,
        50
    )

    print("medium graph done", flush=True)

    avoid_route = calculate_route(
        medium_graph,
        start_node,
        goal_node,
        "risk_cost"
    )

    ################################################
    # 高回避ルート
    ################################################

    print("creating high graph", flush=True)

    high_graph = create_risk_graph(
        G,
        150
    )

    print("creating high done", flush=True)

    avoid_high_route = calculate_route(
        high_graph,
        start_node,
        goal_node,
        "risk_cost"
    )

    ################################################
    # 返却
    ################################################

    return {

        "origin": origin,

        "destination": destination,

        "shortest":
            build_result(
                G,
                shortest_route
            ),

        "avoid":
            build_result(
                medium_graph,
                avoid_route
            ),

        "avoid_high":
            build_result(
                high_graph,
                avoid_high_route
            )
    }