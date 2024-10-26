import pandas as pd
import numpy as np
import networkx as nx
from collections import defaultdict
from itertools import combinations
import os
import scipy.sparse as sp
import joblib
import gzip
from concurrent.futures import ProcessPoolExecutor, as_completed
from tqdm import tqdm

data_file = "../dataset/ProcessedCommentsAll.csv"
weight_threshold = 0.7
num_partitions = 100
num_workers = min(os.cpu_count() - 2, 8)
temp_dir = "../temp/"
os.makedirs(temp_dir, exist_ok=True)

completed_batches = {int(file.split("_")[1].split(".")[0]) for file in os.listdir(temp_dir) if file.startswith("batch_")}

data = pd.read_csv(data_file)
data_splits = np.array_split(data, num_partitions)


def process_batch(data_batch, batch_num):
    local_cooccurrence_dict = defaultdict(lambda: {"neg": 0, "neu": 0, "pos": 0})
    local_node_features = defaultdict(lambda: np.zeros(3))

    for _, row in data_batch.iterrows():
        keywords = str(row["keywords"]).split() if pd.notna(row["keywords"]) else []
        neg, neu, pos = row["neg"], row["neu"], row["pos"]

        for word1, word2 in combinations(keywords, 2):
            local_cooccurrence_dict[(word1, word2)]["neg"] += neg
            local_cooccurrence_dict[(word1, word2)]["neu"] += neu
            local_cooccurrence_dict[(word1, word2)]["pos"] += pos

        for word in keywords:
            local_node_features[word] += np.array([neg, neu, pos])

    # 保存批次数据到临时文件
    batch_file = os.path.join(temp_dir, f"batch_{batch_num}.pkl")
    with open(batch_file, "wb") as f:
        joblib.dump((local_cooccurrence_dict, local_node_features), f)


def main():
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = [
            executor.submit(process_batch, batch, batch_num)
            for batch_num, batch in enumerate(data_splits) if batch_num not in completed_batches
        ]
        for future in tqdm(as_completed(futures), total=num_partitions - len(completed_batches), desc="Processing Batches"):
            future.result()

    global_cooccurrence_dict = defaultdict(lambda: {"neg": 0, "neu": 0, "pos": 0})
    global_node_features = defaultdict(lambda: np.zeros(3))

    for batch_num in range(num_partitions):
        batch_file = os.path.join(temp_dir, f"batch_{batch_num}.pkl")
        if os.path.exists(batch_file):
            with open(batch_file, "rb") as f:
                local_cooccurrence_dict, local_node_features = joblib.load(f)

            for (word1, word2), sentiment_weights in local_cooccurrence_dict.items():
                global_cooccurrence_dict[(word1, word2)]["neg"] += sentiment_weights["neg"]
                global_cooccurrence_dict[(word1, word2)]["neu"] += sentiment_weights["neu"]
                global_cooccurrence_dict[(word1, word2)]["pos"] += sentiment_weights["pos"]

            for word, features in local_node_features.items():
                global_node_features[word] += features

    G = nx.Graph()
    for (word1, word2), sentiment_weights in global_cooccurrence_dict.items():
        total_weight = sentiment_weights["neg"] + sentiment_weights["neu"] + sentiment_weights["pos"]
        if total_weight >= weight_threshold:
            G.add_edge(word1, word2, weight=total_weight)

    for word, features in global_node_features.items():
        if word in G and G.degree[word] > 1:
            G.nodes[word]["features"] = features

    adj_matrix = nx.to_scipy_sparse_matrix(G, weight="weight", format="csr")

    model_dir = "../models/"
    os.makedirs(model_dir, exist_ok=True)
    model_path = os.path.join(model_dir, "sentiment_cooccurrence_graph_compressed.pkl.gz")

    with gzip.open(model_path, "wb") as f:
        joblib.dump((G, adj_matrix), f)
    print(f"Compressed model saved to {model_path}")


if __name__ == "__main__":
    main()