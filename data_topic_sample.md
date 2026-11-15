dbo.sales_contract:
code_old: mã hàng hóa
item_name: tên hàng hóa
Theo hợp đồng (các cột có đuôi contract): quality (số lượng), unit_price_after_tax: đơn giá trúng thầu, amount_after_tax: tổng giá trị
Hoàn thành (các cột có đuôi finished): quality (số lượng), unit_price_after_tax: đơn giá, amount_after_tax: tổng giá trị
Cat_1, cat_2: phân loại
city_name: tỉnh thành (city_name_2 là tên trước sát nhập)
entity: tên cty (có 4 cty)

is_thau_con_hieu_luc: là flag để xem thầu còn hiệu lực hay ko, chủ yếu là dựa vào today và cột finished_date (ngày kết thúc). Ngoài ra còn cột confirmed_date là ngày bắt đầu