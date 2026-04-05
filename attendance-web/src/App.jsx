import { useEffect, useState } from "react";
import { initializeApp } from "firebase/app";
import {
  getFirestore,
  collection,
  onSnapshot,
  query,
  orderBy
} from "firebase/firestore";

const firebaseConfig = {
  apiKey: "AIzaSyCDBG4wECmxqmrcX5dkhBAA_r6bFTg4KCE",
  authDomain: "smart-attendance-ai-7139f.firebaseapp.com",
  projectId: "smart-attendance-ai-7139f",
  storageBucket: "smart-attendance-ai-7139f.firebasestorage.app",
  messagingSenderId: "452085670379",
  appId: "1:452085670379:web:1b7c62b26133c2c06c79ae",
};

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);

function App() {
  const [dataList, setDataList] = useState([]);

  useEffect(() => {
    const q = query(collection(db, "attendance"), orderBy("time", "desc"));

    const unsubscribe = onSnapshot(q, (snapshot) => {
      const temp = [];
      snapshot.forEach((doc) => {
        temp.push(doc.data());
      });
      setDataList(temp);
    });

    return () => unsubscribe();
  }, []);

  return (
    <div style={{ padding: "20px", background: "#0b1a2b", minHeight: "100vh", color: "white" }}>
      <h1>🔥 Smart Attendance</h1>

      <table style={{ width: "100%" }}>
        <thead>
          <tr>
            <th>Face</th>
            <th>Name</th>
            <th>Subject</th>
            <th>Time</th>
          </tr>
        </thead>

        <tbody>
          {dataList.map((data, index) => (
            <tr key={index}>
              <td>
                <img
                  src={data.image}
                  width="80"
                  alt="face"
                  onError={(e) => e.target.src = "https://via.placeholder.com/80"}
                />
              </td>
              <td>{data.name}</td>
              <td>{data.subject}</td>
              <td>{data.time}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default App;