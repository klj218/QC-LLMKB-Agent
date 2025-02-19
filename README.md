# QC-LLMKB-Agent

## 项目简介

QC-DS-API 是一个基于 Python 的数据流 API 服务，主要用于实时获取并转发腾讯接口返回的流式数据。项目通过调用腾讯提供的接口，将流数据逐行返回给前端或其他客户端，适用于需要长时间保持连接、实时更新数据的场景。此外，本项目使用 Gunicorn 部署，支持生产环境下高并发访问，同时通过合理的超时设置和反向代理方案（如 Nginx）保障稳定性。

## 技术栈

- Python 3.11
- Flask / Werkzeug（或 WSGI 框架，根据你的项目实际选择）
- Requests / Urllib3（用于 HTTP 流式请求）
- Gunicorn（用于生产环境部署）

## 项目结构

```plaintext
QC-DS-API/
├── app.py              # 主应用入口，包含流式 API 的具体实现
├── gunicorn_config.py  # Gunicorn 配置文件（可选）
├── requirements.txt    # 项目依赖列表
└── README.md           # 项目说明文档
```

## 安装说明

1. **克隆仓库**

   ```bash
   git clone https://github.com/BTDXBTDX/QC-LLMKB-Agent.git
   cd QC-LLMKB-Agent
   ```

2. **创建虚拟环境**

   建议使用 Python 自带的 venv 工具：

   ```bash
   python3.11 -m venv venv
   source venv/bin/activate   # Linux 或 macOS
   # Windows: venv\Scripts\activate
   ```

3. **安装依赖**

   如果项目中提供了 `requirements.txt`，可使用如下命令安装依赖：

   ```bash
   pip install -r requirements.txt
   ```

## 使用方法

### 1. 开发环境运行

如果只是进行本地开发调试，可以直接运行项目。例如，假设 `app.py` 文件中包含如下启动代码：

```python
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
```

则可以直接执行：

```bash
python app.py
```

然后在浏览器中访问 `http://localhost:8000/stream`（或其他 API 路径）即可。

### 2. 生产环境运行

为了应对长连接和高并发环境，建议使用 Gunicorn 部署。

#### 2.1 使用命令行参数启动

例如，设置超时时间为 600 秒，worker 数量为 1（调试时可降低 worker 数量便于观察日志）：

```bash
gunicorn --timeout 600 -w 1 app:app
```

其中：
- `--timeout 600`：将超时时间设置为 600 秒，适用于长时间数据传输的场景。
- `-w 1`：设置 worker 数量为 1；生产环境下可根据需要调整该参数。
- `app:app`：假设项目中 Flask 应用实例名为 `app`。

#### 2.2 使用 Gunicorn 配置文件

你可以创建一个 `gunicorn_config.py` 文件，将 Gunicorn 的配置参数写入其中：

```python
bind = "0.0.0.0:8000"   # 绑定地址及端口，可根据实际情况修改
workers = 1             # 如果需要调试，可先设置为 1，实际部署时可适当增加
timeout = 600           # 超时时间 600 秒
loglevel = "debug"      # 设置日志级别为 debug，便于调试问题
```

启动时指定配置文件：

```bash
gunicorn -c gunicorn_config.py app:app
```

### 3. Nginx 反向代理（可选）

在生产环境中，为了更好地管理长连接以及实现 SSL 终端，可以在前端使用 Nginx 作为反向代理。下面是一个简单的 Nginx 配置示例：

```conf
server {
    listen 80;
    server_name your_domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_connect_timeout 600;
        proxy_send_timeout 600;
        proxy_read_timeout 600;
        proxy_buffering off;  # 适合流数据传输
    }
}
```

配置完成后，重载 Nginx 即可生效。


## 贡献

欢迎大家提出建议、报告问题或提交 Pull Request。
在提交代码前，请确保代码风格一致并经过充分测试。

## License

本项目遵循 MIT License，详细内容请参见 [LICENSE](LICENSE) 文件。

---

如有任何疑问或建议，请通过 Issue 联系项目维护者。
