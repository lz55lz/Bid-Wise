"""领域模块根目录。

每个领域独立拥有 API、service、repository、schema 与 model；模块之间通过服务层
协作，禁止跨领域直接操作对方仓储或 ORM 细节。
"""
