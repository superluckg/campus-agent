import { BugOutlined, CommentOutlined, DatabaseOutlined } from "@ant-design/icons";
import { ConfigProvider, Layout, Menu, Space, Typography } from "antd";
import { useMemo } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { ChatPage } from "./pages/ChatPage";
import { DeveloperPage } from "./pages/DeveloperPage";
import { DatabasePage } from "./pages/DatabasePage";
import { MemoryPage } from "./pages/MemoryPage";

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const selected = useMemo(() => {
    if (location.pathname.startsWith("/database")) return "database";
    if (location.pathname.startsWith("/developer")) return "developer";
    if (location.pathname.startsWith("/memories")) return "memories";
    return "chat";
  }, [location]);
  return (
    <ConfigProvider theme={{ token: { colorPrimary: "#0b6bcb", borderRadius: 10 } }}>
      <Layout className="app-shell">
        <Layout.Header className="topbar">
          <Space>
            <div className="brand-mark">Q</div>
            <Typography.Title level={4}>青科大校园生活助手</Typography.Title>
          </Space>
          <Menu
            mode="horizontal"
            selectedKeys={[selected]}
            onClick={({ key }) => navigate(`/${key}`)}
            items={[
              { key: "chat", label: "对话", icon: <CommentOutlined /> },
              { key: "memories", label: "长期记忆", icon: <DatabaseOutlined /> },
              { key: "developer", label: "开发者", icon: <BugOutlined /> },
              { key: "database", label: "数据库", icon: <DatabaseOutlined /> }
            ]}
          />
        </Layout.Header>
        <Routes>
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/memories" element={<MemoryPage />} />
          <Route path="/developer" element={<DeveloperPage />} />
          <Route path="/database" element={<DatabasePage />} />
          <Route path="*" element={<Navigate to="/developer" replace />} />
        </Routes>
      </Layout>
    </ConfigProvider>
  );
}
