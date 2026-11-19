// Mặc định gọi API thật; thêm ?mock=1 để dùng dữ liệu giả khi phát triển.
export const CONFIG = {
  USE_MOCK: new URLSearchParams(location.search).get("mock") === "1",
  API_BASE: "http://localhost:8000",
};
