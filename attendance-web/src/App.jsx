import { useEffect, useState } from "react";
import { collection, onSnapshot, query, orderBy } from "firebase/firestore";
import { db } from "./firebase";

function App() {
  const [data, setData] = useState([]);
  const [search, setSearch] = useState("");

  useEffect(() => {
    const q = query(collection(db, "attendance"), orderBy("time", "desc"));

    const unsubscribe = onSnapshot(q, (snapshot) => {
      const list = [];
      snapshot.forEach((doc) => list.push(doc.data()));
      setData(list);
    });

    return () => unsubscribe();
  }, []);

  const getStatus = (time) => {
    return time > "09:10:00" ? "Late" : "Present";
  };

  const filtered = data.filter((d) =>
    d.name?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div style={{ padding: "30px" }}>

      {/* HEADER */}
      <h1 style={{ fontWeight: "700", fontSize: "28px" }}>
        🔥 Smart Attendance AI
      </h1>
      <p style={{ opacity: 0.6, marginBottom: "25px" }}>
        Live Face Recognition System
      </p>

      {/* CURRENT CLASS */}
      <div className="glass" style={{
        padding: "25px",
        marginBottom: "20px",
        background: "linear-gradient(135deg,#0ea5e9,#6366f1)"
      }}>
        <p style={{ opacity: 0.7 }}>Current Class</p>
        <h2 style={{ fontSize: "26px", fontWeight: "700" }}>
          TEST CLASS 🔥
        </h2>
      </div>

      {/* SEARCH */}
      <input
        placeholder="🔍 Search student..."
        onChange={(e) => setSearch(e.target.value)}
      />

      {/* TABLE */}
      <div className="glass" style={{ padding: "15px" }}>
        <table>
          <thead>
            <tr>
              <th>Face</th>
              <th>Name</th>
              <th>USN</th>
              <th>Time</th>
              <th>Status</th>
            </tr>
          </thead>

          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan="5" style={{ textAlign: "center", padding: "20px" }}>
                  🚫 No Data Found
                </td>
              </tr>
            ) : (
              filtered.map((item, i) => {
                const status = getStatus(item.time);

                return (
                  <tr key={i}>
                    <td>
                      <img
                        src={item.image}
                        style={{
                          width: "45px",
                          height: "45px",
                          borderRadius: "50%",
                          objectFit: "cover"
                        }}
                      />
                    </td>

                    <td>{item.name}</td>
                    <td>{item.usn || "N/A"}</td>
                    <td>{item.time}</td>

                    <td>
                      <span
                        className="status"
                        style={{
                          background:
                            status === "Late"
                              ? "#ef4444"
                              : "#22c55e"
                        }}
                      >
                        {status}
                      </span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

    </div>
  );
}

export default App;