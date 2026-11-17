"""整数最大流与最小费用最大流：把共享余位下的可服务人数问题建成二部网络。

网络结构为 居民组 -> 可参加课程 -> 汇点，所有容量为整数，
计算结果是“最多可服务多少人”的能力边界，而不是某一次确定的报名分配。
边费用承载课程偏好权重，用于在不做报名假设的前提下给出
偏好口径下的可达权重上界。仅使用标准库。
"""

from collections import deque


class FlowNetwork:
    def __init__(self):
        self._adjacency = {}

    def node(self, name):
        if name not in self._adjacency:
            self._adjacency[name] = []
        return name

    def add_edge(self, source, target, capacity, cost=0):
        if capacity < 0:
            raise ValueError('capacity must be non-negative')
        self.node(source)
        self.node(target)
        # [目标, 反向边下标, 剩余容量, 原始容量, 单位费用]
        forward = [target, len(self._adjacency[target]), int(capacity),
                   int(capacity), int(cost)]
        backward = [source, len(self._adjacency[source]), 0, 0, -int(cost)]
        self._adjacency[source].append(forward)
        self._adjacency[target].append(backward)
        return forward

    def max_flow(self, source, sink):
        self.node(source)
        self.node(sink)
        total = 0
        while True:
            level = {source: 0}
            queue = deque([source])
            while queue:
                name = queue.popleft()
                for edge in self._adjacency[name]:
                    target, _, residual, _, _ = edge
                    if residual > 0 and target not in level:
                        level[target] = level[name] + 1
                        queue.append(target)
            if sink not in level:
                return total
            iterator = {name: 0 for name in self._adjacency}

            def push(node, amount):
                if node == sink:
                    return amount
                edges = self._adjacency[node]
                while iterator[node] < len(edges):
                    edge = edges[iterator[node]]
                    target, reverse_index, residual, _, _ = edge
                    if residual > 0 and level.get(target) == level[node] + 1:
                        moved = push(target, min(amount, residual))
                        if moved:
                            edge[2] -= moved
                            self._adjacency[target][reverse_index][2] += moved
                            return moved
                    iterator[node] += 1
                return 0

            while True:
                pushed = push(source, 10 ** 12)
                if not pushed:
                    break
                total += pushed

    def min_cost_max_flow(self, source, sink):
        """SPFA 增广：先保证最大流，再在同流量下取最小费用。

        返回 (最大流量, 最小总费用)。费用为负时等价于最大化偏好权重。
        """
        self.node(source)
        self.node(sink)
        total_flow = 0
        total_cost = 0
        infinity = 10 ** 18
        while True:
            distance = {name: infinity for name in self._adjacency}
            previous = {}
            in_queue = set()
            distance[source] = 0
            queue = deque([source])
            in_queue.add(source)
            while queue:
                name = queue.popleft()
                in_queue.discard(name)
                for index, edge in enumerate(self._adjacency[name]):
                    target, _, residual, _, cost = edge
                    if residual <= 0:
                        continue
                    candidate = distance[name] + cost
                    if candidate < distance[target]:
                        distance[target] = candidate
                        previous[target] = (name, index)
                        if target not in in_queue:
                            queue.append(target)
                            in_queue.add(target)
            if sink not in previous:
                return total_flow, total_cost
            amount = infinity
            node = sink
            while node != source:
                parent, edge_index = previous[node]
                amount = min(amount, self._adjacency[parent][edge_index][2])
                node = parent
            node = sink
            while node != source:
                parent, edge_index = previous[node]
                edge = self._adjacency[parent][edge_index]
                target, reverse_index, _, _, cost = edge
                edge[2] -= amount
                self._adjacency[target][reverse_index][2] += amount
                total_cost += cost * amount
                node = parent
            total_flow += amount

    def edge_flows(self, source):
        """返回从 source 出发各条边当前已通过的流量：{目标节点: 流量}。"""
        result = {}
        for edge in self._adjacency.get(source, ()):
            target, _, residual, capacity, _ = edge
            result[target] = capacity - residual
        return result

    def inflow(self, node):
        """返回进入 node 的各条边当前已通过的流量：{来源节点: 流量}。

        邻接表中 node 上与某来源对应的是反向边，其剩余容量即已通过流量。
        """
        result = {}
        for edge in self._adjacency.get(node, ()):
            source, _, residual, capacity, _ = edge
            if capacity == 0:  # 反向边：初始容量为 0，剩余量等于已通过流量
                result[source] = residual
        return result
