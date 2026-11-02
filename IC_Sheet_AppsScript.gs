// ================== Nitin IC Bot — Google Sheet नोंदी (Web App) ==================
// १) हा पूर्ण कोड Apps Script मध्ये पेस्ट करा  २) SECRET हवा असल्यास बदला
// ३) Deploy → New deployment → Web app → Execute as: Me, Who has access: Anyone
const SECRET = "NitinIC2026";   // setup_extras.py मध्ये हाच शब्द टाकायचा

const ORD_HEAD = ["Trade ID", "Mode", "Date-Time", "Type", "Leg", "Strike", "CE/PE", "Side", "Qty",
                  "Price", "Booked P&L", "Order ID", "Status", "Note"];
const TRD_HEAD = ["Trade ID", "Mode", "Status", "Entry Time", "Expiry", "Rollover", "Short CE", "Short PE",
                  "Hedge CE", "Hedge PE", "Shifts", "Booked P&L", "Unbooked P&L", "Net P&L",
                  "Last Update", "Exit Time", "Exit Reason"];

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const d = JSON.parse(e.postData.contents);
    if (d.secret !== SECRET) return out_({ok: false, err: "secret"});
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    if (d.kind === "order") addOrder_(ss, d);
    else if (d.kind === "trade") upsertTrade_(ss, d);
    return out_({ok: true});
  } catch (err) {
    return out_({ok: false, err: String(err)});
  } finally {
    lock.releaseLock();
  }
}

function doGet() {
  return out_({ok: true, msg: "IC Bot sheet चालू आहे"});
}

function sheet_(ss, name, head) {
  let sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.getRange(1, 1, 1, head.length).setValues([head])
      .setFontWeight("bold").setBackground("#1f3b57").setFontColor("#ffffff");
    sh.setFrozenRows(1);
  }
  return sh;
}

function addOrder_(ss, d) {
  const sh = sheet_(ss, "Orders", ORD_HEAD);
  sh.appendRow([d.id, d.mode, d.time, d.type, d.leg, d.strike, d.opt, d.side, d.qty,
                d.price, d.booked, d.order_id, d.status, d.note]);
  const r = sh.getLastRow();
  if (d.status === "FAILED") sh.getRange(r, 1, 1, ORD_HEAD.length).setBackground("#f8d7da");
  sh.getRange(r, 8).setFontColor(d.side === "SELL" ? "#c0392b" : "#1e8449").setFontWeight("bold");
  sh.getRange(r, 10, 1, 2).setNumberFormat("#,##0.00");
}

function upsertTrade_(ss, d) {
  const sh = sheet_(ss, "Trades", TRD_HEAD);
  const row = [d.id, d.mode, d.status, d.entry_time, d.expiry, d.rollover, d.short_ce, d.short_pe,
               d.hedge_ce, d.hedge_pe, d.shifts, d.booked, d.unbooked, d.net, d.updated,
               d.exit_time, d.exit_reason];
  const last = sh.getLastRow();
  let r = 0;
  if (last > 1) {
    const ids = sh.getRange(2, 1, last - 1, 1).getValues();
    for (let i = ids.length - 1; i >= 0; i--) {
      if (ids[i][0] === d.id) { r = i + 2; break; }
    }
  }
  if (r) sh.getRange(r, 1, 1, row.length).setValues([row]);
  else { sh.appendRow(row); r = sh.getLastRow(); }
  const bg = d.status === "OPEN" ? "#d4efdf" : (d.status === "CLOSED" ? "#eaeded" : "#fdebd0");
  sh.getRange(r, 1, 1, row.length).setBackground(bg);
  sh.getRange(r, 12, 1, 3).setNumberFormat("₹#,##0.00");
  const net = Number(d.net);
  if (d.net !== "" && !isNaN(net)) {
    sh.getRange(r, 14).setFontColor(net >= 0 ? "#1e8449" : "#c0392b").setFontWeight("bold");
  }
}

function out_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}
