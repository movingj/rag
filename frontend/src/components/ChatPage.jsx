import { useState } from 'react';
import { Button, Card, Empty, Input, InputNumber, List, Space, Spin, Tag, Typography, message } from 'antd';
import { SendOutlined } from '@ant-design/icons';
import { api } from '../api';

export default function ChatPage() {
  const [question, setQuestion] = useState('');
  const [topK, setTopK] = useState(3);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [elapsed, setElapsed] = useState(null);

  const ask = async () => {
    if (!question.trim()) {
      message.warning('请输入问题');
      return;
    }
    setLoading(true);
    const started = performance.now();
    try {
      const data = await api.query(question.trim(), topK);
      setResult(data);
      setElapsed(((performance.now() - started) / 1000).toFixed(2));
    } catch (err) {
      message.error(`问答失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Card title="智能问答（RAG）">
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space wrap>
            <Input.TextArea
              rows={3}
              style={{ width: 640, maxWidth: '80vw' }}
              placeholder="例如：什么是 RAG？"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onPressEnter={(e) => {
                if (e.metaKey || e.ctrlKey) ask();
              }}
            />
          </Space>
          <Space>
            <span>引用条数 Top-K：</span>
            <InputNumber min={1} max={10} value={topK} onChange={(v) => setTopK(v || 3)} />
            <Button type="primary" icon={<SendOutlined />} loading={loading} onClick={ask}>
              提问
            </Button>
            <Typography.Text type="secondary">⌘/Ctrl + Enter 快捷发送</Typography.Text>
          </Space>
        </Space>
      </Card>

      {loading && <Spin tip="正在检索并生成答案…"><div style={{ height: 80 }} /></Spin>}

      {!loading && result && (
        <>
          <Card
            title="回答"
            extra={elapsed && <Tag color="blue">耗时 {elapsed}s</Tag>}
          >
            <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', fontSize: 16 }}>
              {result.answer}
            </Typography.Paragraph>
          </Card>
          <Card title={`引用来源（${result.sources.length} 条）`}>
            <List
              dataSource={result.sources}
              renderItem={(item, index) => (
                <List.Item>
                  <List.Item.Meta
                    title={
                      <Space>
                        <Tag color="geekblue">[{index + 1}]</Tag>
                        <Tag color="green">相似度 {item.score.toFixed(3)}</Tag>
                      </Space>
                    }
                    description={item.text}
                  />
                </List.Item>
              )}
            />
          </Card>
        </>
      )}

      {!loading && !result && <Empty description="输入问题，体验检索增强生成" />}
    </Space>
  );
}
