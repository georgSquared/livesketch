import typing
from typing import Protocol, List

import networkx as nx
import numpy as np
from numpy import ndarray
from sklearn.metrics import roc_auc_score
from sklearn.metrics.pairwise import cosine_similarity

from bipartite_embeddings.constants import SampleType, EdgeOperator, SimilarityMeasure
from bipartite_embeddings.utils import (
    get_train_test_samples,
    DotDict,
    performance_measuring,
)


class EmbeddingModel(Protocol):
    def fit(self, g: nx.Graph):
        pass

    def get_embedding(self) -> ndarray:
        pass


class ClassifierEstimator(Protocol):
    def fit(self, x: List[ndarray], y: ndarray):
        pass

    def predict(self, x: List[ndarray]) -> ndarray:
        pass

    def predict_proba(self, x: List[ndarray]) -> ndarray:
        pass


# TODO: Add performance measuring in the correct spots


class Evaluator:
    def __init__(
        self,
        graph: nx.Graph,
        embedding_model: EmbeddingModel,
        classifier: typing.Optional[ClassifierEstimator] = None,
        embedding_operator: typing.Optional[EdgeOperator] = None,
    ):
        self.graph = graph
        self.embedding_model = embedding_model
        self.classifier = classifier
        self.embedding_operator = embedding_operator
        self._samples = None

    def get_samples(self) -> DotDict:
        return get_train_test_samples(self.graph)

    @property
    def samples(self):
        if not self._samples:
            self._samples = self.get_samples()

        return self._samples

    def get_node_embeddings(
        self, sample_type: typing.Optional[SampleType] = None
    ) -> ndarray:
        if sample_type == SampleType.TRAIN:
            self.embedding_model.fit(self.samples.G_train)

        elif sample_type == SampleType.TEST:
            self.embedding_model.fit(self.samples.G_test)

        else:
            self.embedding_model.fit(self.graph)

        return self.embedding_model.get_embedding()

    @property
    def train_node_embeddings(self) -> ndarray:
        return self.get_node_embeddings(sample_type=SampleType.TRAIN)

    @property
    def test_node_embeddings(self) -> ndarray:
        return self.get_node_embeddings(sample_type=SampleType.TEST)

    def get_edge_embeddings(self, sample_type: SampleType) -> List[ndarray]:
        node_embeddings = (
            self.train_node_embeddings
            if sample_type == SampleType.TRAIN
            else self.test_node_embeddings
        )
        samples = (
            self.samples.edge_ids_train
            if sample_type == SampleType.TRAIN
            else self.samples.edge_ids_test
        )

        if self.embedding_operator == EdgeOperator.CONCAT:
            return [
                np.concatenate((node_embeddings[edge[0]], node_embeddings[edge[1]]))
                for edge in samples
            ]

        # This translates to using different node operations to compute the edge embeddings
        # i.e. Cosine Similarity, Hadamard Product, Jaccard Distance, etc.
        elif self.embedding_operator == EdgeOperator.HADAMARD:
            return [
                np.multiply(node_embeddings[edge[0]], node_embeddings[edge[1]])
                for edge in samples
            ]

        elif self.embedding_operator == EdgeOperator.AVERAGE:
            return [
                (node_embeddings[edge[0]] + node_embeddings[edge[1]]) / 2
                for edge in samples
            ]

        raise ValueError(f"Unknown edge operator: {self.embedding_operator}")

    def get_roc_auc_score(self) -> float:
        if not self.classifier or not self.embedding_operator:
            raise ValueError(
                "ROC AUC score can only be computed when classifier and embedding operator are both set"
            )

        # Get the edge embeddings for the train Graph, and fit the classifier
        train_edge_embeddings = self.get_edge_embeddings(sample_type=SampleType.TRAIN)
        self.classifier.fit(train_edge_embeddings, self.samples.edge_labels_train)

        # Get the edge embeddings for the test Graph, and predict the labels of the test data
        test_edge_embeddings = self.get_edge_embeddings(sample_type=SampleType.TEST)
        predictions = self.classifier.predict_proba(test_edge_embeddings)[:, 1]

        # Compute the ROC AUC score
        score = roc_auc_score(self.samples.edge_labels_test, predictions)

        return score

    def get_precision_at_100(
        self, similarity_measure: SimilarityMeasure = SimilarityMeasure.HAMMING
    ) -> float:
        with performance_measuring(message="Node embeddings calculation"):
            node_embeddings = self.get_node_embeddings()

        if similarity_measure == SimilarityMeasure.COSINE:
            similarities = cosine_similarity(node_embeddings)
        elif similarity_measure == SimilarityMeasure.HAMMING:
            # This calculates distances instead of similarities
            similarities = (node_embeddings[:, None, :] == node_embeddings).sum(2)

        elif similarity_measure == SimilarityMeasure.DOT_PRODUCT:
            similarities = np.dot(node_embeddings, node_embeddings.T)

        else:
            raise ValueError(f"Unknown similarity measure: {similarity_measure}")

        # Define a mask to filter out the upper triangular matrix (including the diagonal)
        similarities = np.tril(similarities, k=-1)

        indices = np.argpartition(similarities.ravel(), -100)[-100:]
        indices = indices[np.argsort(similarities.ravel()[indices])][::-1]
        top100_indices = np.unravel_index(indices, similarities.shape)

        top100_indices = list(zip(top100_indices[0], top100_indices[1]))

        for idx in top100_indices:
            print(f"Value at index: {idx} is {similarities[idx[0], idx[1]]}")

        tp = 0
        for idx in top100_indices:
            # Print the similarity between the two nodes
            print(
                f"{similarity_measure} Similarity between {idx[0]} and {idx[1]}: {similarities[idx]}"
            )
            print(f"Node embeddings of node {idx[0]}: {node_embeddings[idx[0]]}")
            print(f"Node embeddings of node {idx[1]}: {node_embeddings[idx[1]]}")

            if self.graph.has_edge(idx[0], idx[1]):
                tp += 1
                print(f"Edge exists between {idx[0]} and {idx[1]}. TP: {tp}")
                print("\n")

        return tp / 100
