// Google Apps Script - Backend for Charity White Goods Order Management
// Deploy this as a web app from Google Apps Script editor

const SHEET_NAME = 'Orders';

function getSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    sheet.appendRow([
      'ID', 'Created', 'Updated', 'Status',
      'Recipient Name', 'Recipient Phone', 'Recipient Address', 'Recipient Postcode',
      'Item Type', 'Item Description', 'Assigned To', 'Delivery Date', 'Notes'
    ]);
    sheet.getRange(1, 1, 1, 13).setFontWeight('bold');
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function generateId() {
  return 'ORD-' + new Date().getTime().toString(36).toUpperCase();
}

function rowToOrder(headers, row) {
  const obj = {};
  headers.forEach((h, i) => {
    obj[h] = row[i] || '';
  });
  return obj;
}

function doGet(e) {
  return handleRequest(e);
}

function doPost(e) {
  return handleRequest(e);
}

function handleRequest(e) {
  const params = e.parameter || {};
  const action = params.action || 'list';

  try {
    let result;
    switch (action) {
      case 'list':
        result = listOrders(params);
        break;
      case 'get':
        result = getOrder(params.id);
        break;
      case 'create':
        result = createOrder(JSON.parse(e.postData.contents));
        break;
      case 'update':
        result = updateOrder(params.id, JSON.parse(e.postData.contents));
        break;
      case 'delete':
        result = deleteOrder(params.id);
        break;
      default:
        result = { error: 'Unknown action: ' + action };
    }
    return ContentService.createTextOutput(JSON.stringify(result))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ error: err.message }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function listOrders(params) {
  const sheet = getSheet();
  const data = sheet.getDataRange().getValues();
  if (data.length <= 1) return { orders: [] };

  const headers = data[0];
  let orders = data.slice(1).map(row => rowToOrder(headers, row));

  // Optional status filter
  if (params.status && params.status !== 'all') {
    orders = orders.filter(o => o.Status === params.status);
  }

  return { orders: orders };
}

function getOrder(id) {
  const sheet = getSheet();
  const data = sheet.getDataRange().getValues();
  const headers = data[0];

  for (let i = 1; i < data.length; i++) {
    if (data[i][0] === id) {
      return { order: rowToOrder(headers, data[i]) };
    }
  }
  return { error: 'Order not found' };
}

function createOrder(body) {
  const sheet = getSheet();
  const now = new Date().toISOString();
  const id = generateId();

  const row = [
    id,
    now,
    now,
    body.status || 'Pending',
    body.recipientName || '',
    body.recipientPhone || '',
    body.recipientAddress || '',
    body.recipientPostcode || '',
    body.itemType || '',
    body.itemDescription || '',
    body.assignedTo || '',
    body.deliveryDate || '',
    body.notes || ''
  ];

  sheet.appendRow(row);
  return { success: true, id: id };
}

function updateOrder(id, body) {
  const sheet = getSheet();
  const data = sheet.getDataRange().getValues();

  for (let i = 1; i < data.length; i++) {
    if (data[i][0] === id) {
      const rowNum = i + 1;
      const now = new Date().toISOString();

      // Update only provided fields
      if (body.status !== undefined) sheet.getRange(rowNum, 4).setValue(body.status);
      if (body.recipientName !== undefined) sheet.getRange(rowNum, 5).setValue(body.recipientName);
      if (body.recipientPhone !== undefined) sheet.getRange(rowNum, 6).setValue(body.recipientPhone);
      if (body.recipientAddress !== undefined) sheet.getRange(rowNum, 7).setValue(body.recipientAddress);
      if (body.recipientPostcode !== undefined) sheet.getRange(rowNum, 8).setValue(body.recipientPostcode);
      if (body.itemType !== undefined) sheet.getRange(rowNum, 9).setValue(body.itemType);
      if (body.itemDescription !== undefined) sheet.getRange(rowNum, 10).setValue(body.itemDescription);
      if (body.assignedTo !== undefined) sheet.getRange(rowNum, 11).setValue(body.assignedTo);
      if (body.deliveryDate !== undefined) sheet.getRange(rowNum, 12).setValue(body.deliveryDate);
      if (body.notes !== undefined) sheet.getRange(rowNum, 13).setValue(body.notes);

      // Always update the "Updated" timestamp
      sheet.getRange(rowNum, 3).setValue(now);

      return { success: true };
    }
  }
  return { error: 'Order not found' };
}

function deleteOrder(id) {
  const sheet = getSheet();
  const data = sheet.getDataRange().getValues();

  for (let i = 1; i < data.length; i++) {
    if (data[i][0] === id) {
      sheet.deleteRow(i + 1);
      return { success: true };
    }
  }
  return { error: 'Order not found' };
}
