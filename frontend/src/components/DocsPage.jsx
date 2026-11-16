import { useCallback, useEffect, useState } from 'react';
import { Button, Card, Checkbox, InputNumber, Popconfirm, Space, Statistic, Table, Tag, Typography, Upload, message } from 'antd';
import { DeleteOutlined, InboxOutlined, ReloadOutlined } from '@ant-design/icons';
import { api, ingestFile } from '../api';
import { listImports, cancelImport } from '../api';

const STATUS_TAG = {
  pending: { color: 'default', text: '排队中' },
  processing: { color: 'processing', text: '处理中' },
  completed: { color: 'success', text: '已完成' },
  failed: { color: 'error', text: '失败' },
  cancelled: { color: 'warning', text: '已取消' },
};

const STAGE_TEXT = { queued: '排队', parsing: '解析文档', embedding: '向量化入库', done: '完成', cancelled: '已取消' };

export default function DocsPage({ stats, onIngested }) {
  const [chunk, setChunk] = useState(false);
  const [chunkSize, setChunkSize] = useState(500);
  const [chunkOverlap, setChunkOverlap] = useState(50);
  const [docs, setDocs] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [tasks, setTasks] = useState([]);

  const refreshTasks = useCallback(async () => {
    try {
      const data = await listImports();
      setTasks(data);
      return data;
    } catch {
      return [];
    }
  }, []);

  const refreshDocs = useCallback(async () => {
    setListLoading(true);
    try {
      const data = await api.documents(200);
      setDocs(data.documents);
    } catch (err) {
      message.error(`获取文档列表失败：${err.message}`);
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshTasks();
  }, [refreshTasks]);

  useEffect(() => {
    const hasActive = tasks.some((t) => t.status === 'pending' || t.status === 'processing');
    if (!hasActive) return;
    const timer = setInterval(async () => {
      const data = await refreshTasks();
      if (!data.some((t) => t.status === 'pending' || t.status === 'processing')) {
        refreshDocs();
        onIngested();
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [tasks, refreshTasks, refreshDocs, onIngested]);

  useEffect(() => {
    refreshDocs();
  }, [refreshDocs]);

  const remove = async (id) => {
    try {
      await api.deleteDocument(id);
      message.success(`已删除文档 ${id}`);
      onIngested();
      refreshDocs();
    } catch (err) {
      message.error(`删除失败：${err.message}`);
    }
  };

  const uploadProps = {
    accept: '.pdf,.docx,.doc,.txt,.md',
    showUploadList: false,
    customRequest: async ({ file, onSuccess, onError }) => {
      try {
        const data = await ingestFile(file, chunk ? { chunk, chunk_size: chunkSize, chunk_overlap: chunkOverlap } : { chunk: false });
        message.success(`「${data.filename}」已创建导入任务，可在下方查看进度`);
        refreshTasks();
        onSuccess(data);
      } catch (err) {
        message.error(`「${file.name}」导入失败：${err.message}`, 6);
        onError(err);
      }
    },
  };

  const cancelTask = async (id) => {
    try {
      await cancelImport(id);
      message.success('已发送取消指令');
      refreshTasks();
    } catch (err) {
      message.error(`取消失败：${err.message}`);
    }
  };

  const taskColumns = [
    { title: '文件名', dataIndex: 'filename', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (status) => <Tag color={STATUS_TAG[status]?.color}>{STATUS_TAG[status]?.text || status}</Tag>,
    },
    { title: '阶段', dataIndex: 'stage', width: 110, render: (stage) => STAGE_TEXT[stage] || stage },
    { title: '解析字数', dataIndex: 'parsed_chars', width: 90 },
    { title: '写入片段', dataIndex: 'chunks', width: 90 },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 160,
      render: (ts) => new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false }),
    },
    {
      title: '操作',
      width: 100,
      render: (_, record) =>
        record.status === 'pending' || record.status === 'processing' ? (
          <Popconfirm title="确认取消该导入任务？" onConfirm={() => cancelTask(record.id)}>
            <Button danger size="small">取消</Button>
          </Popconfirm>
        ) : record.status === 'failed' ? (
          <Typography.Text type="danger" style={{ fontSize: 12 }}>{record.error}</Typography.Text>
        ) : null,
    },
  ];

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 90, render: (id) => <code>{id}</code> },
    { title: '文档内容', dataIndex: 'text', ellipsis: true },
    {
      title: '操作',
      width: 100,
      render: (_, record) => (
        <Popconfirm title={`确认删除文档 ${record.id}？`} onConfirm={() => remove(record.id)}>
          <Button danger size="small" icon={<DeleteOutlined />}>删除</Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Card>
        <Statistic title="知识库文档总数" value={stats ? stats.documents : '-'} />
      </Card>
      <Card title="导入文件（PDF / DOCX / DOC / TXT / MD）">
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space wrap>
            <Checkbox checked={chunk} onChange={(e) => setChunk(e.target.checked)}>
              启用文档切分
            </Checkbox>
            {chunk && (
              <>
                <span>切分长度：</span>
                <InputNumber min={50} max={8000} value={chunkSize} onChange={(v) => setChunkSize(v || 500)} />
                <span>重叠长度：</span>
                <InputNumber min={0} max={1000} value={chunkOverlap} onChange={(v) => setChunkOverlap(v ?? 50)} />
              </>
            )}
          </Space>
          <Upload.Dragger {...uploadProps}>
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">点击或拖拽文件到此处上传</p>
            <p className="ant-upload-hint">
              自动解析正文与表格，扫描件 PDF 自动 OCR 识别；按上方切分设置入库
            </p>
          </Upload.Dragger>
        </Space>
      </Card>
      <Card
        title="导入任务"
        extra={<Button icon={<ReloadOutlined />} onClick={refreshTasks}>刷新</Button>}
      >
        <Table
          rowKey="id"
          columns={taskColumns}
          dataSource={tasks}
          pagination={{ pageSize: 5 }}
          locale={{ emptyText: '暂无导入任务' }}
        />
      </Card>
      <Card
        title="文档列表"
        extra={<Button icon={<ReloadOutlined />} onClick={refreshDocs}>刷新</Button>}
      >
        <Table
          rowKey="id"
          columns={columns}
          dataSource={docs}
          loading={listLoading}
          pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 条` }}
        />
      </Card>
    </Space>
  );
}
