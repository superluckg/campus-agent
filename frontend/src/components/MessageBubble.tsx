import { FileTextOutlined, LinkOutlined, RobotOutlined, UserOutlined } from "@ant-design/icons";
import { Avatar, Card, Space, Spin, Tag, Typography } from "antd";
import ReactMarkdown from "react-markdown";

import type { Message } from "../types";
import { formatSourcePages } from "../utils/sourcePages";
import { citationSuperscripts } from "../utils/citationSuperscript";

export function MessageBubble({ message, progress }: { message: Message; progress?: string }) {
  const assistant = message.role === "assistant";
  const showCard = !progress || !!message.content || message.display_metadata.incomplete === true
    || message.citations.length > 0;
  return (
    <div className={`message-row ${assistant ? "assistant" : "user"}`}>
      <Avatar icon={assistant ? <RobotOutlined /> : <UserOutlined />} />
      <div className="message-column">
        {assistant && message.agent_names.length > 0 && (
          <Space wrap className="agent-tags">
            {message.agent_names.map((agent) => <Tag key={agent}>{agent}</Tag>)}
          </Space>
        )}
        {assistant && progress && <div className="message-progress" role="status" aria-live="polite">
          <Spin size="small" /><span>{progress}</span>
        </div>}
        {showCard && <Card className="message-card" size="small">
          {message.content && <ReactMarkdown rehypePlugins={
            message.display_metadata.render_citations === true
              ? [[citationSuperscripts, { indexes: message.citations.map(c => c.citation_index) }]] : []
          }>{message.content}</ReactMarkdown>}
          {typeof message.display_metadata.citation_warning === "string" && (
            <Typography.Paragraph type="warning">{message.display_metadata.citation_warning}</Typography.Paragraph>
          )}
          {message.display_metadata.generating === true && (
            <Typography.Text type="secondary">正在生成…</Typography.Text>
          )}
          {message.display_metadata.incomplete === true && (
            <Typography.Paragraph type="warning">
              {String(message.display_metadata.error_message || "本次回答未完成，以上内容是草稿。")}
            </Typography.Paragraph>
          )}
          {message.citations.length > 0 && (
            <div className="citations">
              <Typography.Text strong><FileTextOutlined /> 参考来源</Typography.Text>
              {message.citations.map((citation) => (
                <div key={`${citation.citation_index}-${citation.title}`}>
                  <Typography.Text type="secondary">
                    [{citation.citation_index}] {citation.title}
                    {formatSourcePages(citation.page_numbers, citation.page_number)
                      ? `，${formatSourcePages(citation.page_numbers, citation.page_number)}` : ""}
                  </Typography.Text>
                  {citation.source_url && (
                    <a href={citation.source_url} target="_blank" rel="noreferrer">
                      <LinkOutlined /> 官网
                    </a>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>}
      </div>
    </div>
  );
}
