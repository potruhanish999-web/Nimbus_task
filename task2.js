db = db.getSiblingDB('nimbus_events');

*/---CLEANING THE DATA----/*
const parseTime = {
  $cond: [
    { $eq: [{ $type: '$timestamp' }, 'date'] },
    '$timestamp',
    { $cond: [
      { $regexMatch: { input: '$timestamp', regex: '^\\d\\d/\\d\\d/\\d{4}' } },
      { $dateFromString: { dateString: '$timestamp', format: '%m/%d/%Y %H:%M:%S' } },

      { $dateFromString: { dateString: { $replaceOne: { input: '$timestamp', find: ' ', replacement: 'T' } } } } ] } ]
};

db.user_activity_logs.aggregate([
  { $addFields: {
      customer: { $toInt: { $ifNull: ['$customer_id', '$customerId'] } },
      user:     { $toInt: { $ifNull: ['$member_id', { $ifNull: ['$memberId', { $ifNull: ['$userId', '$userID'] }] }] } },
      time: parseTime } },
  { $group: { _id: { customer: '$customer', user: '$user', time: '$time', type: '$event_type', page: '$page_url' },
              doc: { $first: '$$ROOT' } } },
  { $replaceRoot: { newRoot: '$doc' } },
  { $lookup: { from: 'sql_customers', localField: 'customer', foreignField: 'customer_id', as: 'sql' } },
  { $addFields: { tier: { $first: '$sql.plan_tier' } } },
  { $project: { customer: 1, user: 1, time: 1, event_type: 1, feature: 1, tier: 1,
                session_duration_sec: 1, _id: 0 } },
  { $out: 'events' }
]);
print('clean events:', db.events.countDocuments(), 'of', db.user_activity_logs.countDocuments());

*/---Q1----/*

print('\nQ1');
db.events.aggregate([
  { $match: { tier: { $ne: null } } },
  { $group: { _id: { user: '$user', tier: '$tier', day: { $dateTrunc: { date: '$time', unit: 'day' } } },
              duration: { $max: '$session_duration_sec' } } },
  { $group: { _id: { user: '$_id.user', tier: '$_id.tier',
                     week: { $dateTrunc: { date: '$_id.day', unit: 'week' } } },
              sessions: { $sum: 1 } } },
  { $group: { _id: '$_id.tier', avg_sessions_per_user_per_week: { $avg: '$sessions' } } },
  { $sort: { _id: 1 } }
]).forEach(printjson);

db.events.aggregate([
  { $match: { tier: { $ne: null } } },
  { $group: { _id: { user: '$user', tier: '$tier', day: { $dateTrunc: { date: '$time', unit: 'day' } } },
              duration: { $max: '$session_duration_sec' } } },
  { $group: { _id: '$_id.tier',
              p25_p50_p75: { $percentile: { input: '$duration', p: [0.25, 0.5, 0.75], method: 'approximate' } } } },
  { $sort: { _id: 1 } }
]).forEach(printjson);

*/---Q2A----/*

print('\nQ2a');
db.events.aggregate([
  { $match: { feature: { $ne: null } } },
  { $group: { _id: { feature: '$feature', day: { $dateTrunc: { date: '$time', unit: 'day' } } },
              users: { $addToSet: '$user' } } },
  { $group: { _id: '$_id.feature', avg_daily_active_users: { $avg: { $size: '$users' } } } },
  { $sort: { avg_daily_active_users: -1 } }
]).forEach(printjson);

*/---Q2B----/*

print('\nQ2b');
db.events.aggregate([
  { $match: { feature: { $ne: null } } },
  { $group: { _id: { feature: '$feature', user: '$user' }, times: { $push: '$time' }, first: { $min: '$time' } } },
  { $addFields: { came_back: { $anyElementTrue: { $map: { input: '$times', as: 't', in: { $and: [
      { $gt: ['$$t', '$first'] },
      { $lte: ['$$t', { $dateAdd: { startDate: '$first', unit: 'day', amount: 7 } }] } ] } } } } } },
  { $group: { _id: '$_id.feature', users: { $sum: 1 }, came_back: { $sum: { $cond: ['$came_back', 1, 0] } } } },
  { $project: { users: 1, came_back: 1,
                retention_7d_pct: { $round: [{ $multiply: [100, { $divide: ['$came_back', '$users'] }] }, 1] } } },
  { $sort: { retention_7d_pct: -1 } }
]).forEach(printjson);

*/---Q3----/*

print('\nQ3');
const steps = ['signup', 'first_login', 'workspace_created', 'first_project', 'invited_teammate'];
const first = {};
steps.forEach(s => first[s] = { $min: { $cond: [{ $eq: ['$step', s] }, '$time', '$$REMOVE'] } });

const accounts = db.onboarding_events.aggregate([
  { $addFields: {
      customer: { $toInt: { $ifNull: ['$customer_id', '$customerId'] } },
      time: parseTime } },
  { $group: Object.assign({ _id: '$customer' }, first) }
]).toArray();

function median(a) { a.sort((x, y) => x - y); return a.length ? a[Math.floor(a.length / 2)] : null; }
let previous = null;
steps.forEach((s, i) => {
  const reached = accounts.filter(u => u[s]);
  const hours = i === 0 ? [] :
    accounts.filter(u => u[s] && u[steps[i - 1]]).map(u => (u[s] - u[steps[i - 1]]) / 3600000).filter(h => h >= 0);
  print(s.padEnd(18), 'customers:', reached.length,
        previous === null ? '' : 'drop-off: ' + (100 * (1 - reached.length / previous)).toFixed(1) + '%',
        i === 0 ? '' : 'median hours from previous step: ' + (median(hours) === null ? 'n/a' : median(hours).toFixed(1)));
  previous = reached.length;
});

*/---Q4----/*

print('\nQ4');
const latest = db.events.find().sort({ time: -1 }).limit(1).toArray()[0].time;
const since = new Date(latest.getTime() - 90 * 24 * 3600 * 1000);
db.events.aggregate([
  { $match: { tier: 'free' } },
  { $group: { _id: '$customer',
              features: { $addToSet: '$feature' },
              days: { $addToSet: { $dateTrunc: { date: '$time', unit: 'day' } } },
              recent_events: { $sum: { $cond: [{ $gte: ['$time', since] }, 1, 0] } } } },
  { $project: { customer: '$_id', _id: 0,
                features_used: { $size: { $filter: { input: '$features', cond: { $ne: ['$$this', null] } } } },
                active_days: { $size: '$days' }, recent_events: 1 } },
  { $addFields: { engagement_score: { $add: [{ $multiply: [3, '$features_used'] },
                                             { $multiply: [2, '$active_days'] }, '$recent_events'] } } },
  { $sort: { engagement_score: -1 } },
  { $limit: 20 }
]).forEach(printjson);
