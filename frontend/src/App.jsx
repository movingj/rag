import { useEffect, useState } from 'react';
import { Layout, Menu, Tag, Typography, message } from 'antd';
import { MessageOutlined, DatabaseOutlined, SearchOutlined, SettingOutlined } from '@ant-design/icons';
import { api } from './api';
import ChatPage from './components/ChatPage';
import DocsPage from './components/DocsPage';
import RetrievePage from './components/RetrievePage';
import SettingsPage from './components/SettingsPage';

const { Header, Content, Footer } = Layout;

export default function App() {
  const [page, setPage] = useState('chat');
  const [stats, setStats] = useState(null);
  const [healthy, setHealthy] = useState(false);

  const refreshStats = async () => {
    try {
      const data = await api.stats();
      setStats(data);
    } catch (err) {
      message.error(`获取统计失败：${err.message}`);
    }
  };

  useEffect(() => {
    api.health().then(() => setHealthy(true)).catch(() => setHealthy(false));
    refreshStats();
  }, []);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
        <Typography.Title level={4} style={{ color: '#fff', margin: 0, whiteSpace: 'nowrap' }}>
          RAG 知识库 Demo
        </Typography.Title>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[page]}
          onClick={({ key }) => setPage(key)}
          style={{ flex: 1, minWidth: 0 }}
          items={[
            { key: 'chat', icon: <MessageOutlined />, label: '智能问答' },
            { key: 'docs', icon: <DatabaseOutlined />, label: '文档管理' },
            { key: 'retrieve', icon: <SearchOutlined />, label: '检索测试' },
            { key: 'settings', icon: <SettingOutlined />, label: 'API 配置' },
          ]}
        />
        <Tag color={healthy ? 'success' : 'error'}>{healthy ? '后端已连接' : '后端未连接'}</Tag>
      </Header>
      <Content style={{ padding: 24, maxWidth: 1100, width: '100%', margin: '0 auto' }}>
        {page === 'chat' && <ChatPage />}
        {page === 'docs' && <DocsPage stats={stats} onIngested={refreshStats} />}
        {page === 'retrieve' && <RetrievePage />}
        {page === 'settings' && <SettingsPage />}
      </Content>
      <Footer style={{ textAlign: 'center', color: '#999' }}>
        FastAPI · React + Ant Design
      </Footer>
    </Layout>
  );
}
