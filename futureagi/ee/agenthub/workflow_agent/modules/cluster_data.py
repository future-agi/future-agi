
import pandas as pd
from pydantic import BaseModel

from agentic_eval.core.database.ch_vector import ClickHouseVectorDB

from typing import Any, Optional

HDBSCAN: Optional[Any] = None

try:
    from hdbscan import HDBSCAN as _HDBSCAN
    HDBSCAN = _HDBSCAN
except ImportError:
    pass

class DataClustererConstants(BaseModel):
    """
    Constants for the DataClusterer class.

    Attributes:
        min_cluster_size (int): The minimum size of clusters.
        min_samples (int): The number of samples in a neighborhood for a point to be considered a core point.
        cluster_selection_epsilon (float): A distance threshold. Clusters below this value will be merged.
        embed_col_name (str): The name of the column containing embeddings.
        cluster_label_col_name (str): The name of the column to store cluster labels.
    """
    min_cluster_size: int = 2
    min_samples: int = 1
    cluster_selection_epsilon: float = 0.2
    embed_col_name: str
    cluster_label_col_name: str


class DataClusterer:
    """
    A class for clustering data using HDBSCAN algorithm.
    """

    def __init__(self, constants: DataClustererConstants) -> None:
        """
        Initialize the DataClusterer.

        Args:
            constants (DataClustererConstants): Constants for clustering.
        """
        self.constants = constants
        if HDBSCAN is None:
            raise ImportError("HDBSCAN is not installed. Please install it with 'pip install hdbscan'")
        self.clusterer = HDBSCAN(
            min_cluster_size=constants.min_cluster_size,
            min_samples=constants.min_samples,
            cluster_selection_epsilon=constants.cluster_selection_epsilon
        )

    def cluster_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Cluster the data in the given DataFrame.

        Args:
            df (pd.DataFrame): The DataFrame containing the data to be clustered.

        Returns:
            pd.DataFrame: The input DataFrame with an additional column for cluster labels.

        Raises:
            ValueError: If the DataFrame doesn't contain the specified embedding column.
        """
        if self.constants.embed_col_name not in df.columns:
            raise ValueError(f"DataFrame must contain an {self.constants.embed_col_name} column")

        embeddings = df[self.constants.embed_col_name].tolist()
        self.clusterer.fit(embeddings)

        if self.constants.cluster_label_col_name in df.columns:
            df = df.drop(columns=[self.constants.cluster_label_col_name])

        df[self.constants.cluster_label_col_name] = self.clusterer.labels_

        return df


def main() -> None:
    """
    Main function to demonstrate the usage of DataClusterer.

    This function:
    1. Fetches data from a ClickHouse database.
    2. Creates a DataFrame from the fetched data.
    3. Initializes a DataClusterer and performs clustering on the data.
    4. Prints information about the resulting clusters.
    """
    db = ClickHouseVectorDB()
    query = '''
        SELECT id, vector, `metadata.key`, `metadata.value`
        FROM `default`.events_embedding
        WHERE deleted = 0
        AND NOT has(`metadata.key`, 'node_id')
    '''
    results = db.fetch_vectors_by_query(query=query)

    # Create a DataFrame from the results
    data = []
    for id, vector, metadata in results:
        row = {'id': id, 'vector': vector}
        row.update(metadata)
        data.append(row)

    df = pd.DataFrame(data)

    # Initialize DataClusterer
    constants = DataClustererConstants(
        embed_col_name='vector',
        cluster_label_col_name='cluster'
    )
    clusterer = DataClusterer(constants)

    # Perform clustering
    clustered_df = clusterer.cluster_data(df)

    print(f"Clustering complete. Number of clusters: {clustered_df['cluster'].nunique()}")

    cluster_sizes = clustered_df['cluster'].value_counts().sort_index()
    for cluster_label, size in cluster_sizes.items():
        print(f"Cluster {cluster_label}: {size} items")


if __name__ == "__main__":
    main()
