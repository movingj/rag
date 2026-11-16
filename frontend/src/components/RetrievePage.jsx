import { useState } from 'react';
import { Button, Card, Input, InputNumber, Space, Table, Tag, message } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { api } from '../api';

export default function RetrievePage() {
  const [q, setQ] = useState('');
  const [topK, setTopK] = useState(3);
  const [loading, setLoading] = useState(false);
  const [hits, setHits] = useState([]);
  const [searched, setSearched] = useState(false);

  const search = async () => {
    if (!q.trim()) {
      message.warning('请输入检索内容');
      return;
    }
    setLoading(true);
    try {
      const data = await api.retrieve(q.trim(), topK);
      setHits(data.hits);
      setSearched(true);
    } catch (err) {
      message.error(`检索失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const columns = [
    {
      title: '相似度',
      dataIndex: 'score',
      width: 140,
      sorter: (a, b) => a.score - b.score,
      render: (score) => <Tag color={score > 0.4 ? 'green' : score > 0.2 ? 'orange' : 'default'}>{score.toFixed(4)}</Tag>,
    },
    { title: '文档内容', dataIndex: 'text' },
    { title: '文档 ID', dataIndex: 'id', width: 300, render: (id) => <code>{id}</code> },
  ];

  return (
    <Card title="检索测试（不调用 LLM）">
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Space wrap>
          <Input.Search
            style={{ width: 480, maxWidth: '70vw' }}
            placeholder="输入检索关键词或问题"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onSearch={search}
            enterButton={<Button type="primary" icon={<SearchOutlined />} loading={loading}>检索</Button>}
          />
          <span>Top-K：</span>
          <InputNumber min={1} max={10} value={topK} onChange={(v) => setTopK(v || 3)} />
        </Space>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={hits}
          loading={loading}
          pagination={false}
          locale={{ emptyText: searched ? '知识库为空或无结果' : '输入内容开始检索' }}
        />
      </Space>
    </Card>
  );
}
