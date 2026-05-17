/**
 * Knock Not — Google Apps Script Calendar Push
 *
 * READ-ONLY — only reads calendar events, never creates or deletes.
 *
 * SETUP:
 *   1. Go to https://script.google.com  →  New Project
 *   2. Paste this entire file into Code.gs
 *   3. ENABLE the Calendar Advanced Service:
 *       - Click "+" next to "Services" (left sidebar)
 *       - Find "Google Calendar API"  →  click Add
 *   4. Edit SERVER_URL below to your server IP
 *   5. FIRST RUN — to grant all permissions:
 *       - Select "authorize" from the function dropdown  →  click Run
 *       - Click "Review permissions"  →  choose your work account
 *       - Click "Advanced"  →  "Go to Knock Not (unsafe)"
 *       - Click "Allow"  (grants calendar + external request permissions)
 *   6. Then run "listAllRooms" to find room calendar IDs
 *   7. Add rooms in the Knock Not dashboard with those IDs
 *   8. Set up a trigger for "pushAllRoomStatus":
 *       - Click Triggers (clock icon)  →  Add Trigger
 *       - Function: pushAllRoomStatus
 *       - Event source: Time-driven  →  Minutes timer  →  Every 1 minute
 */

// ─── CONFIGURE THIS ───
var SERVER_URL = "https://remover-yummy-urgent.ngrok-free.dev";
// ───────────────────────

/**
 * Run this FIRST to grant all permissions (calendar + external requests).
 * It will prompt you to authorize — click through and allow everything.
 */
function authorize() {
  // Touch CalendarApp to trigger calendar permission
  var cals = CalendarApp.getAllCalendars();
  Logger.log("Calendar access OK — found " + cals.length + " subscribed calendars");

  // Touch UrlFetchApp to trigger external request permission
  try {
    var resp = UrlFetchApp.fetch(SERVER_URL + "/api/v1/health", { muteHttpExceptions: true });
    Logger.log("Server connection OK — " + resp.getContentText());
  } catch (e) {
    Logger.log("Server connection test: " + e.toString());
    Logger.log("(This is OK if the server isn't running yet)");
  }

  Logger.log("\nAll permissions granted! Now run 'listAllRooms' next.");
}

/**
 * Find room calendar IDs.
 *
 * HOW TO FIND ROOM IDS MANUALLY (if this script finds 0):
 *   1. Open Google Calendar in your browser
 *   2. Create a new event
 *   3. Click "Add rooms" or "Rooms" tab
 *   4. Browse or search for rooms — you'll see room names
 *   5. Add a room to the event, then click on the room name
 *   6. The calendar ID is shown (ends with @resource.calendar.google.com)
 *   7. Or: click the room → "Open calendar" → Settings → Calendar ID
 *
 * You can also ask someone to check:
 *   Google Admin Console → Directory → Buildings and resources → Resources
 */
function listAllRooms() {
  Logger.log("=== Discovering room calendars ===\n");

  var rooms = [];
  var pageToken = null;

  // List all calendars visible to you (subscribed ones)
  do {
    var response = Calendar.CalendarList.list({
      showHidden: true,
      maxResults: 250,
      pageToken: pageToken
    });
    var items = response.items || [];
    for (var i = 0; i < items.length; i++) {
      var cal = items[i];
      rooms.push({
        id: cal.id,
        name: cal.summary || cal.id,
        is_resource: (cal.id.indexOf("resource.calendar.google.com") !== -1)
      });
    }
    pageToken = response.nextPageToken;
  } while (pageToken);

  var roomResources = rooms.filter(function(r) { return r.is_resource; });
  var otherCals = rooms.filter(function(r) { return !r.is_resource; });

  if (roomResources.length > 0) {
    Logger.log("──── ROOM CALENDARS (" + roomResources.length + " found) ────\n");
    for (var j = 0; j < roomResources.length; j++) {
      Logger.log("  " + roomResources[j].name);
      Logger.log("  Calendar ID: " + roomResources[j].id + "\n");
    }
  } else {
    Logger.log("──── NO ROOM CALENDARS IN YOUR SUBSCRIPTIONS ────\n");
    Logger.log("  This is normal! Room resources don't auto-appear.");
    Logger.log("  To find room calendar IDs:\n");
    Logger.log("  OPTION A — From Google Calendar UI:");
    Logger.log("    1. Open calendar.google.com");
    Logger.log("    2. Create a new event → click 'Rooms' or 'Add rooms'");
    Logger.log("    3. Pick any room → save the event");
    Logger.log("    4. Open the event → click the room name");
    Logger.log("    5. The Calendar ID is in the details\n");
    Logger.log("  OPTION B — Subscribe to a room calendar:");
    Logger.log("    1. Open calendar.google.com → Settings (gear icon)");
    Logger.log("    2. 'Add calendar' → 'Browse resources'");
    Logger.log("    3. Expand your building → check rooms you want");
    Logger.log("    4. Run this function again — they'll appear\n");
    Logger.log("  OPTION C — If you know a room's email:");
    Logger.log("    Just enter it directly in the Knock Not dashboard");
    Logger.log("    as the Calendar Resource ID. The script can check ANY");
    Logger.log("    calendar ID — it doesn't need to be subscribed.\n");
  }

  Logger.log("──── YOUR CALENDARS (" + otherCals.length + ") ────");
  for (var k = 0; k < otherCals.length; k++) {
    Logger.log("  " + otherCals[k].name + "  →  " + otherCals[k].id);
  }

  // Push to server
  try {
    UrlFetchApp.fetch(SERVER_URL + "/api/v1/calendar/discovered", {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify({ calendars: rooms })
    });
    Logger.log("\nCalendar list pushed to server.");
  } catch (e) {
    Logger.log("\n(Server push: " + e.toString() + ")");
  }
}

/**
 * Timed trigger function — runs every 1 minute.
 * Asks server which rooms to check, checks each, pushes results back.
 * Uses FreeBusy API — works for ANY calendar ID, no subscription needed.
 */
function pushAllRoomStatus() {
  var response;
  try {
    response = UrlFetchApp.fetch(SERVER_URL + "/api/v1/calendar/rooms", {
      method: "get",
      muteHttpExceptions: true,
      headers: {
        "ngrok-skip-browser-warning": "true"
      }
    });
  } catch (e) {
    Logger.log("Cannot reach server: " + e.toString());
    return;
  }

  if (response.getResponseCode() !== 200) {
    Logger.log("Server returned " + response.getResponseCode());
    return;
  }

  var data = JSON.parse(response.getContentText());
  var calendarIds = data.calendar_ids || [];

  if (calendarIds.length === 0) {
    Logger.log("No calendar IDs configured on server");
    return;
  }

  var now = new Date();
  var results = [];

  for (var i = 0; i < calendarIds.length; i++) {
    results.push(checkCalendar(calendarIds[i], now));
  }

  try {
    UrlFetchApp.fetch(SERVER_URL + "/api/v1/calendar/push", {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify({ timestamp: now.toISOString(), rooms: results }),
      muteHttpExceptions: true
    });
    Logger.log("Pushed " + results.length + " rooms (" +
      results.filter(function(r){ return r.busy; }).length + " busy)");
  } catch (e) {
    Logger.log("Push failed: " + e.toString());
  }
}

/**
 * Check a single room calendar using FreeBusy + Events API.
 * FreeBusy works for ANY calendar ID without subscribing.
 */
function checkCalendar(calendarId, now) {
  var result = { calendar_id: calendarId, busy: false, event: null };

  try {
    // Step 1: FreeBusy query — tells us if the room is busy right now
    // Works for ANY calendar, no subscription required
    var fbResponse = Calendar.Freebusy.query({
      timeMin: now.toISOString(),
      timeMax: new Date(now.getTime() + 60000).toISOString(),
      items: [{ id: calendarId }]
    });

    var calData = (fbResponse.calendars || {})[calendarId];
    if (!calData) {
      result.error = "Calendar not found in FreeBusy response";
      return result;
    }

    if (calData.errors && calData.errors.length > 0) {
      result.error = calData.errors[0].reason || "Unknown error";
      return result;
    }

    var busySlots = calData.busy || [];
    if (busySlots.length === 0) {
      return result; // Not busy
    }

    result.busy = true;

    // Step 2: Get event details (title, organizer)
    // This may fail if we don't have read access — that's OK, we still know it's busy
    try {
      var events = Calendar.Events.list(calendarId, {
        timeMin: new Date(now.getTime() - 300000).toISOString(), // 5 min ago
        timeMax: new Date(now.getTime() + 60000).toISOString(),
        singleEvents: true,
        orderBy: "startTime",
        maxResults: 5
      });

      var items = events.items || [];
      for (var i = 0; i < items.length; i++) {
        var ev = items[i];
        var startStr = (ev.start || {}).dateTime;
        var endStr = (ev.end || {}).dateTime;
        if (!startStr || !endStr) continue;

        var start = new Date(startStr);
        var end = new Date(endStr);
        if (start <= now && now <= end) {
          result.event = {
            id: ev.id,
            title: ev.summary || "No title",
            organizer: (ev.organizer || {}).email || "Unknown",
            start: start.toISOString(),
            end: end.toISOString()
          };
          break;
        }
      }
    } catch (detailErr) {
      // Can't get event details — that's OK, we still have busy=true
      Logger.log("Note: Can read busy/free for " + calendarId +
        " but not event details: " + detailErr.toString());
    }

  } catch (e) {
    result.error = e.toString();
  }

  return result;
}

/**
 * Quick test — check a specific room calendar right now.
 * Edit the ID below and run to test.
 */
function testOneRoom() {
  // Replace with an actual room calendar ID to test:
  var testId = "YOUR_ROOM_ID@resource.calendar.google.com";
  var result = checkCalendar(testId, new Date());
  Logger.log(JSON.stringify(result, null, 2));
}
