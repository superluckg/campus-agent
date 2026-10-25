import { DeleteOutlined, EditOutlined } from "@ant-design/icons";
import { Button, Card, Empty, Input, List, Modal, Space, Tag, Typography, message } from "antd";
import { useEffect, useState } from "react";

import { api } from "../api/client";

interface MemoryItem {
  memory_id: string;
  memory_type: string;
  content: string;
  importance: number;
  updated_at: string;
}

export function MemoryPage() {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [editing, setEditing] = useState<MemoryItem>();
  const [content, setContent] = useState("");
  const [messageApi, contextHolder] = message.useMessage();
  const load = async () => setItems(await api<MemoryItem[]>("/memories"));
  useEffect(() => { void load(); }, []);
  return (
    <div className="memory-page">
      {contextHolder}
      <Typography.Title level={2}>我的长期记忆</Typography.Title>
      <Typography.Paragraph type="secondary">
        这里只保存稳定偏好、长期目标、持续任务或你明确要求记住的信息。修改和删除只影响自己的记忆。
      </Typography.Paragraph>
      {!items.length ? <Empty description="目前没有长期记忆" /> : (
        <List grid={{ gutter: 16, column: 3 }} dataSource={items} renderItem={(item) => (
          <List.Item>
            <Card
              title={<Tag>{item.memory_type}</Tag>}
              actions={[
                <EditOutlined key="edit" onClick={() => { setEditing(item); setContent(item.content); }} />,
                <DeleteOutlined key="delete" onClick={() => {
                  Modal.confirm({
                    title: "删除这条长期记忆？",
                    content: "删除后无法从本页面恢复。",
                    okText: "删除",
                    okButtonProps: { danger: true },
                    onOk: async () => {
                      await api(`/memories/${item.memory_id}`, { method: "DELETE" });
                      messageApi.success("已删除");
                      await load();
                    }
                  });
                }} />
              ]}
            >
              <Typography.Paragraph>{item.content}</Typography.Paragraph>
              <Typography.Text type="secondary">重要度 {item.importance.toFixed(2)}</Typography.Text>
            </Card>
          </List.Item>
        )} />
      )}
      <Modal
        title="编辑长期记忆"
        open={Boolean(editing)}
        okText="保存"
        onCancel={() => setEditing(undefined)}
        onOk={async () => {
          if (!editing || !content.trim()) return;
          await api(`/memories/${editing.memory_id}`, {
            method: "PATCH",
            body: JSON.stringify({ content: content.trim() })
          });
          setEditing(undefined);
          messageApi.success("已保存");
          await load();
        }}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text type="secondary">请只保留真实、稳定、未来仍有用的信息。</Typography.Text>
          <Input.TextArea rows={5} value={content} onChange={(event) => setContent(event.target.value)} />
        </Space>
      </Modal>
    </div>
  );
}
