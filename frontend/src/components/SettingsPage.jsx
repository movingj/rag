import { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  InputNumber,
  Row,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  ApiOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  DatabaseOutlined,
  ReloadOutlined,
  RobotOutlined,
  SaveOutlined,
} from '@ant-design/icons';
import { api } from '../api';

function SectionForm({ form, title, icon, accent, testState, onTest }) {
  const result = testState?.result;
  return (
    <Card
      title={
        <Space style={{ color: accent }}>
          {icon}
          <span style={{ color: 'rgba(0,0,0,0.88)' }}>{title}</span>
        </Space>
      }
      style={{ height: '100%' }}
    >
      <Form form={form} layout="vertical" autoComplete="off">
        <Form.Item
          label="Base URL"
          name="base_url"
          rules={[{ required: true, message: '请输入 Base URL' }]}
        >
          <Input placeholder="例如 https://api.voyageai.com/v1" />
        </Form.Item>
        <Form.Item
          label="API Key"
          name="api_key"
          extra="已保存的 key 会掩码显示；不修改（保持掩码或留空）则沿用原 key"
        >
          <Input.Password placeholder="sk-..." autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          label="模型名"
          name="model"
          rules={[{ required: true, message: '请输入模型名' }]}
        >
          <Input placeholder="例如 voyage-4-lite / auto / gpt-4o-mini" />
        </Form.Item>
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item label="RPM（每分钟请求数）" name="rpm" extra="留空表示不限速">
              <InputNumber min={0} style={{ width: '100%' }} placeholder="不限速" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item label="TPM（每分钟 token 数）" name="tpm" extra="留空表示不限速">
              <InputNumber min={0} style={{ width: '100%' }} placeholder="不限速" />
            </Form.Item>
          </Col>
        </Row>
      </Form>
      <Divider style={{ margin: '4px 0 16px' }} />
      <Space align="center">
        <Button icon={<ApiOutlined />} loading={testState?.loading} onClick={onTest}>
          测试连接
        </Button>
        {result &&
          (result.ok ? (
            <Tag color="success" icon={<CheckCircleOutlined />}>
              {result.message}
            </Tag>
          ) : (
            <Tooltip title={result.message}>
              <Tag color="error" icon={<CloseCircleOutlined />} style={{ maxWidth: 300 }}>
                <span style={{ display: 'inline-block', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom' }}>
                  {result.message}
                </span>
              </Tag>
            </Tooltip>
          ))}
        {result && result.ok && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            测试通过后可放心保存
          </Typography.Text>
        )}
      </Space>
    </Card>
  );
}

export default function SettingsPage() {
  const [chatForm] = Form.useForm();
  const [embeddingForm] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [chatTest, setChatTest] = useState(null);
  const [embeddingTest, setEmbeddingTest] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const cfg = await api.getConfig();
      chatForm.setFieldsValue(cfg.chat);
      embeddingForm.setFieldsValue(cfg.embedding);
    } catch (err) {
      message.error(`加载配置失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const makeTester = (section, form, setter) => async () => {
    try {
      await form.validateFields(['base_url', 'model']);
    } catch {
      return; // Base URL / 模型名 必填校验未过
    }
    setter({ loading: true, result: null });
    try {
      const values = form.getFieldsValue();
      const res = await api.testConfig({
        section,
        base_url: values.base_url,
        api_key: values.api_key,
        model: values.model,
      });
      setter({ loading: false, result: res });
    } catch (err) {
      setter({ loading: false, result: { ok: false, message: err.message } });
    } finally {
      // 输入变化后旧结果不再准确，5 秒后自动淡出由用户重新测试即可（不强制清除）
    }
  };

  const save = async () => {
    let chat;
    let embedding;
    try {
      [chat, embedding] = await Promise.all([
        chatForm.validateFields(),
        embeddingForm.validateFields(),
      ]);
    } catch {
      return; // 表单校验失败，antd 已高亮提示
    }
    setSaving(true);
    try {
      const res = await api.saveConfig({ chat, embedding });
      chatForm.setFieldsValue(res.config.chat);
      embeddingForm.setFieldsValue(res.config.embedding);
      message.success('配置已保存并立即生效（无需重启服务）');
      // 保存后旧测试结果不再代表当前线上配置，清掉避免误导
      setChatTest(null);
      setEmbeddingTest(null);
    } catch (err) {
      message.error(`保存失败：${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Spin spinning={loading}>
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message="大模型 API 配置"
          description="配置保存到 backend/config.json 并立即热生效，无需重启后端。Chat 与 Embedding 可分别使用不同的服务、密钥和模型。填好后可先「测试连接」验证服务可用，再保存。"
        />
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <SectionForm
              form={chatForm}
              title="对话模型（Chat）"
              icon={<RobotOutlined />}
              accent="#1677ff"
              testState={chatTest}
              onTest={makeTester('chat', chatForm, setChatTest)}
            />
          </Col>
          <Col xs={24} lg={12}>
            <SectionForm
              form={embeddingForm}
              title="向量化模型（Embedding）"
              icon={<DatabaseOutlined />}
              accent="#52c41a"
              testState={embeddingTest}
              onTest={makeTester('embedding', embeddingForm, setEmbeddingTest)}
            />
          </Col>
        </Row>
        <Space>
          <Button type="primary" size="large" icon={<SaveOutlined />} loading={saving} onClick={save}>
            保存配置
          </Button>
          <Button size="large" icon={<ReloadOutlined />} onClick={load} disabled={saving}>
            重新加载
          </Button>
        </Space>
      </Space>
    </Spin>
  );
}
