---
title: "Từ animation đến doanh thu: Hướng dẫn bán asset 3D trên FAB (kèm Cascadeur)"
slug: "huong-dan-ban-asset-3d-tren-fab"
excerpt: "Toàn bộ hành trình của một creator asset 3D: tạo animation nhanh bằng Cascadeur, mở tài khoản bán hàng trên FAB, đóng gói & đăng sản phẩm, nhận doanh thu và xử lý thuế thu nhập (W-8BEN)."
category: "Game Dev"
tags: ["Cascadeur", "FAB", "3D Assets", "Game Dev", "Animation", "Thuế"]
readingTime: 10
featured: true
publishedAt: "2026-07-25"
seoTitle: "Hướng dẫn tạo animation Cascadeur & bán asset 3D trên FAB (A-Z)"
seoDescription: "Hướng dẫn từ A-Z: tạo animation bằng Cascadeur, mở tài khoản FAB, đóng gói và đăng sản phẩm, nhận doanh thu 88% và khai thuế thu nhập W-8BEN cho người bán ở Việt Nam."
---

# Từ animation đến doanh thu: Hướng dẫn bán asset 3D trên FAB (kèm Cascadeur)

Nếu bạn đang làm asset 3D — nhân vật, animation, môi trường, prop — thì **FAB** (chợ nội dung chính thức của Epic Games, gộp từ Unreal Marketplace, Sketchfab, Quixel và ArtStation Marketplace) là nơi bán tự nhiên nhất hiện nay. Bài này đi qua trọn vẹn một vòng: **làm animation → mở tài khoản bán → đăng sản phẩm → nhận tiền → nộp thuế.**

> ⚠️ **Lưu ý nhanh:** Phần thuế bên dưới là kiến thức tổng quan, không phải tư vấn thuế cá nhân hoá. Con số và thủ tục có thể thay đổi theo thời điểm — nên kiểm tra lại tài liệu chính thức của FAB và tham khảo kế toán trước khi kê khai.

---

## 1. Tạo animation nhanh bằng Cascadeur

[Cascadeur](https://cascadeur.com/) là phần mềm animation keyframe có **AI hỗ trợ**, mạnh nhất ở mảng chuyển động vật lý (nhảy, đấm, ngã, hành động). Điểm hay là bạn không cần rig phức tạp mà vẫn ra được chuyển động "thật" nhờ mấy tính năng AI:

- **AutoPosing** — chỉnh vài điểm điều khiển, AI tự cân bằng tư thế toàn thân cho tự nhiên.
- **AutoPhysics** — phân tích chuyển động theo trọng lực/lực/quán tính và tự thêm chuyển động phụ (secondary motion) như tay vung khi nhảy, follow-through của cú đá.
- **AI Inbetweening** (từ bản 2025.1) — tự sinh các frame trung gian nối giữa các pose chính, mượt hơn hẳn.

### Quy trình cơ bản (blocking → physics)

1. **Cài đặt & mở project** — tải bản Free (đủ dùng cho cá nhân) từ trang chủ, import model/rig hoặc dùng nhân vật mẫu.
2. **Blocking** — đặt các pose chính (key pose) trên timeline: pose bắt đầu, cao trào, kết thúc.
3. **AutoPosing** — dùng để chốt nhanh từng pose cho cân đối, đúng trọng tâm.
4. **Spacing & timing** — chỉnh khoảng cách frame để ra nhịp chuyển động.
5. **AutoPhysics** — bật để hệ thống tự thêm quán tính và secondary motion; tinh chỉnh bằng các slider/filter.
6. **Polish & export** — làm mượt, rồi export ra `.fbx` để đưa vào Unreal/Unity hoặc đóng gói bán.

### Video nên xem (từ dễ đến nâng cao)

- 🎬 [Get Started in Cascadeur — Your First Animation](https://www.youtube.com/watch?v=nUADUrQf97c) — chính chủ, ~13 phút, làm animation đầu tiên từ số 0.
- ⚡ [Your first Cascadeur animation in 5 MINUTES](https://www.youtube.com/watch?v=dztpzcyXMDw) — cực nhanh, 3 animation cơ bản.
- 📚 [Everything About Cascadeur (Full Free Course)](https://www.youtube.com/watch?v=vPwVGuYEk2o) — crash course ~26 phút: interface, posing, tools, workflow.
- 🧪 [Everything about AutoPhysics (Physics Pipeline 2025)](https://www.youtube.com/watch?v=QPPIxzbH1o8) — đào sâu công cụ physics.
- 📺 [Playlist tutorial chính thức của Cascadeur](https://www.youtube.com/playlist?list=PLXcuot7sDvCtyH16zo2f3JJFlNGNfLzwg) và trang học [cascadeur.com/tutor](https://cascadeur.com/tutor).

---

## 2. Tạo tài khoản bán hàng trên FAB.com

Bán trên FAB đi qua tài khoản **Epic Games**. Các bước:

1. **Đăng ký / đăng nhập Epic Games** tại [fab.com](https://www.fab.com/) — có thể dùng email hoặc liên kết Google/Apple.
2. Bấm **“Publish”** trên thanh công cụ để bắt đầu quy trình trở thành người bán.
3. **Chấp nhận Fab Distribution Agreement** (thỏa thuận phân phối). Đọc kỹ điều khoản trước khi đồng ý.
4. **Tạo Creator Code** — mã/username duy nhất, đồng thời là đường dẫn trang publisher của bạn (`fab.com/sellers/<creator-code>`).
5. **Hoàn tất Publisher Profile** — avatar, tên hiển thị, mô tả.
6. **Trader Verification** — xác minh danh tính người bán; sau đó khai **thông tin thuế (tax)** và **thanh toán (payout)**. Yêu cầu cụ thể khác nhau theo quốc gia (xem mục 4).

> 💡 Nên chuẩn bị sẵn: giấy tờ tùy thân, thông tin tài khoản nhận tiền (PayPal/ngân hàng theo phương thức FAB hỗ trợ tại VN), và thông tin để điền form thuế W-8BEN.

---

## 3. Đóng gói, đăng sản phẩm & nhận doanh thu

### 3.1. Đóng gói sản phẩm (packaging)

- **Chuẩn hoá file**: export đúng định dạng (`.fbx`, `.glb`, texture `.png/.tga`, material...). Với asset cho Unreal thì đóng gói theo cấu trúc project/plugin đúng chuẩn engine.
- **Đặt tên rõ ràng & sạch**: tên file/thư mục nhất quán, xoá file thừa, file tạm.
- **Kèm tài liệu**: một file README ngắn (cách import, scale, license) giúp giảm refund và tăng review tốt.
- **Kiểm tra bản quyền**: mọi texture, âm thanh, model bên trong phải là của bạn hoặc có license cho phép bán lại.

### 3.2. Đăng sản phẩm (listing)

1. **Create a Listing** trong dashboard publisher.
2. **Upload asset(s)** — một listing có thể gồm nhiều asset để tạo thành một product.
3. **Media set** — bắt buộc có **thumbnail** + **tối thiểu 1 ảnh khác hoặc bản xem trước 3D**. Ảnh đẹp = tỉ lệ mua cao hơn nhiều; nên có render đẹp + ảnh wireframe/scale reference.
4. **Điền thông tin**: tiêu đề, mô tả, tag, danh mục, engine tương thích, **giá** (hoặc Free), license.
5. **Submit for review** — FAB duyệt trước khi lên kệ. Sản phẩm đạt yêu cầu kỹ thuật & nội dung mới được publish.

### 3.3. Nhận doanh thu (payout)

- **Tỉ lệ chia**: bạn nhận **88% doanh thu** từ sản phẩm bán ra (FAB giữ 12%).
- **Ngưỡng chi trả**: FAB trả tiền **~30 ngày sau khi kết thúc tháng**, nhưng chỉ khi số dư đạt **từ 100 USD trở lên**. Chưa đủ thì cộng dồn sang các tháng sau.
- **Refund**: các khoản hoàn tiền được duyệt sẽ bị **trừ vào kỳ payout tiếp theo**.

> Ví dụ: sản phẩm giá $20 → mỗi lượt bán bạn nhận ~$17.6 (trước khi trừ thuế khấu lưu ở mục 4).

---

## 4. Thuế thu nhập từ FAB

Đây là phần nhiều người bỏ qua rồi bị "hụt tiền" khi nhận payout. Có **hai tầng thuế**:

### 4.1. Thuế khấu lưu tại Mỹ (US withholding) — form W-8BEN

Epic/FAB coi doanh thu bán asset là **royalty (tiền bản quyền)**. Người bán **ngoài nước Mỹ** phải khai:

- **Form W-8BEN** — dành cho **cá nhân** nước ngoài.
- **Form W-8BEN-E** — dành cho **doanh nghiệp/tổ chức** nước ngoài.

Form này xác nhận bạn không phải người nộp thuế Mỹ và xác định mức **thuế khấu lưu trên phần doanh thu có nguồn từ Mỹ** (các lượt mua bởi khách ở Mỹ).

**Điểm quan trọng cho người bán ở Việt Nam:**
- **Việt Nam hiện KHÔNG có hiệp định tránh đánh thuế hai lần (tax treaty) đang có hiệu lực với Mỹ.** Vì vậy bạn **không được hưởng mức ưu đãi treaty**, và phần doanh thu **nguồn Mỹ** thường bị khấu lưu ở mức mặc định **30%**.
- Phần doanh thu **không phải nguồn Mỹ** (khách mua ở nước khác) **không bị** khấu lưu thuế Mỹ.
- Khai W-8BEN **chính xác và đầy đủ** vẫn quan trọng: khai sai/để trống có thể khiến toàn bộ payout bị giữ hoặc bị khấu lưu nặng hơn. (Có nhiều báo cáo người bán bị kẹt ở bước tax setup do form sinh ra chưa đúng — kiên nhẫn làm lại theo đúng loại cá nhân/doanh nghiệp.)

### 4.2. Thuế thu nhập tại Việt Nam

Thu nhập từ FAB là **thu nhập từ nước ngoài** và về nguyên tắc **phải kê khai tại Việt Nam**:

- Cá nhân kiếm tiền online thường thuộc diện **thuế thu nhập cá nhân / hộ kinh doanh** — thường gồm **thuế GTGT + TNCN** theo tỷ lệ trên doanh thu (mức cụ thể tùy ngưỡng doanh thu và cách bạn đăng ký).
- Nên **giữ lại toàn bộ chứng từ**: sao kê payout FAB, số tiền đã bị khấu lưu ở Mỹ, tỷ giá — để kê khai và tránh rủi ro bị truy thu.
- Vì không có hiệp định VN–Mỹ, phần thuế đã bị Mỹ khấu lưu **khó được khấu trừ** khi tính thuế ở VN → càng cần tính toán kỹ để không bị đánh thuế chồng.

> ✅ **Khuyến nghị thực tế:** ngay khi doanh thu bắt đầu ổn định, hãy (1) điền W-8BEN đúng ngay từ đầu, (2) đăng ký hình thức kinh doanh/kê khai phù hợp ở VN, và (3) gặp một kế toán rành thuế thu nhập từ nước ngoài. Chi phí tư vấn nhỏ hơn nhiều so với tiền phạt/truy thu.

---

## Tóm tắt

| Bước | Việc cần làm | Ghi nhớ chính |
|------|--------------|----------------|
| 1. Animation | Blocking → AutoPosing → AutoPhysics → export FBX | Dùng bản Cascadeur Free là đủ bắt đầu |
| 2. Tài khoản | Publish → chấp nhận agreement → Creator Code → verify | Dùng tài khoản Epic Games |
| 3. Đăng bán | Đóng gói sạch → media đẹp → submit review | Nhận **88%**, payout khi ≥ **$100**, ~30 ngày sau tháng |
| 4. Thuế | W-8BEN (cá nhân) / W-8BEN-E (doanh nghiệp) + kê khai ở VN | VN không có treaty với Mỹ → khấu lưu ~**30%** phần nguồn Mỹ |

Làm asset tốt chỉ là một nửa câu chuyện — nửa còn lại là dựng quy trình bán và quản lý dòng tiền cho gọn. Chúc bạn ra sản phẩm đầu tiên sớm! 🚀

---

*Nguồn tham khảo:*
- *[Fab — Publisher Get Started](https://dev.epicgames.com/documentation/fab/publisher-get-started-in-fab)*
- *[Fab Distribution Agreement](https://www.fab.com/distribution-agreement)*
- *[Epic to unify content marketplaces (88% revenue)](https://www.gamedeveloper.com/marketing/epic-to-unify-content-marketplaces-and-offer-creators-88-percent-revenue-cut)*
- *[Cascadeur — Learn / Tutorials](https://cascadeur.com/tutor)*
