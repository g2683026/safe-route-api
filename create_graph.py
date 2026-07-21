import osmnx as ox

G = ox.graph_from_place(
    "Tsuzuki-ku, Yokohama, Japan",
    network_type="walk"
)

G = ox.project_graph(G)

ox.save_graphml(
    G,
    "graph.graphml"
)

print("completed")